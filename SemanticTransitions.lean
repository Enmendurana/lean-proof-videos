import Lean
import LeanTeX

namespace Animate

open Lean Meta

/-- A half-open character interval in the canonical LaTeX state. -/
structure SemanticSpan where
  start : Nat
  «end» : Nat
deriving ToJson, FromJson, BEq, Repr

/-- One occurrence in an elaborated expression, linked back to rendered LaTeX. -/
structure SemanticNode where
  id : String
  kind : String
  identity : String := ""
  fingerprint : String
  parentId : Option String := none
  path : String
  latexSpans : Array SemanticSpan := #[]
deriving ToJson, FromJson, BEq, Repr

structure SemanticEdge where
  sourceNodeId : String
  targetNodeId : String
  reason : String
  confidence : Float
deriving ToJson, FromJson, BEq, Repr

/-- The logical correspondence used by the animation.  Character matching is
    deliberately not part of this structure. -/
structure SemanticTransition where
  sourceNodes : Array SemanticNode
  targetNodes : Array SemanticNode
  edges : Array SemanticEdge
  proofKind : String
  adapter : String
  proofFingerprint : String := ""
  proofTerm : String := ""
  proofDescendants : Array String := #[]
  proofPremises : Array String := #[]
  proofConstants : Array String := #[]
  fallbackReason : Option String := none
deriving ToJson, FromJson, Repr

private def exprKind : Expr → String
  | .bvar _ => "bvar"
  | .fvar _ => "fvar"
  | .mvar _ => "mvar"
  | .sort _ => "sort"
  | .const _ _ => "const"
  | .app _ _ => "app"
  | .lam _ _ _ _ => "lambda"
  | .forallE _ _ _ _ => "forall"
  | .letE _ _ _ _ _ => "let"
  | .lit _ => "literal"
  | .mdata _ _ => "metadata"
  | .proj _ _ _ => "projection"

private def exprIdentity : Expr → String
  | .fvar id => "fvar:" ++ id.name.toString
  | .mvar id => "mvar:" ++ id.name.toString
  | .bvar index => "bvar:" ++ toString index
  | .const name _ => "const:" ++ name.toString
  | .lit literal => "literal:" ++ reprStr literal
  | .proj name index _ => s!"projection:{name}:{index}"
  | _ => ""

private def exprChildren : Expr → Array Expr
  | .app fn arg => #[fn, arg]
  | .lam _ type body _ => #[type, body]
  | .forallE _ type body _ => #[type, body]
  | .letE _ type value body _ => #[type, value, body]
  | .mdata _ body => #[body]
  | .proj _ _ body => #[body]
  | _ => #[]

private def startsWithChars (haystack needle : List Char) : Bool :=
  match needle, haystack with
  | [], _ => true
  | _, [] => false
  | n :: ns, h :: hs => n == h && startsWithChars hs ns

private def isIdentifierChar (character : Char) : Bool :=
  character.isAlphanum || character == '_' || character == '\''

/-- A one-letter mathematical identifier must not be taken from the middle of
    a LaTeX command (`f` in `\forall`) or a longer identifier. -/
private def occurrenceHasTokenBoundaries
    (haystack needle : List Char) (start : Nat) : Bool :=
  let first := needle.head?
  if !first.any isIdentifierChar then true
  else
    let previousOk := if start == 0 then true else
      let previous := haystack[start - 1]!
      previous != '\\' && !isIdentifierChar previous
    let after := start + needle.length
    let nextOk := if after >= haystack.length then true else
      !isIdentifierChar haystack[after]!
    previousOk && nextOk

private def allOccurrences (haystack needle : String) : Array SemanticSpan := Id.run do
  let hs := haystack.toList
  let ns := needle.toList
  if ns.isEmpty then return #[]
  let mut result := #[]
  for start in [0 : hs.length] do
    if startsWithChars (hs.drop start) ns && occurrenceHasTokenBoundaries hs ns start then
      result := result.push { start, «end» := start + ns.length }
  return result

private structure RawNode where
  id : String
  kind : String
  identity : String
  fingerprint : String
  parentId : Option String
  path : String
  latex : String

private def escapedBinderName (name : Name) : String :=
  name.toString.replace "_" "\\_"

private partial def collectRawNodes
    (rootId path : String) (parentId : Option String) (expr : Expr)
    (boundNames : Array String := #[]) : MetaM (Array RawNode) := do
  let expr := expr.consumeMData
  let id := rootId ++ "/" ++ path
  let latex ← match expr with
    | .bvar index =>
      match boundNames[index]? with
      | some name => pure name
      | none => LeanTeX.run_latexPP expr {}
    | _ => LeanTeX.run_latexPP expr {}
  -- Bodies are traversed with de Bruijn variables so their semantic paths stay
  -- faithful to the original expression.  Calling `whnf` on such a detached
  -- subexpression reports a Lean panic even though rendering can safely use its
  -- structural hash; normalize only closed/local-context-valid expressions.
  let normalized ← if expr.hasLooseBVars then pure expr else
    try whnf expr catch _ => pure expr
  let current : RawNode := {
    id
    kind := exprKind expr
    identity := exprIdentity expr
    fingerprint := toString normalized.consumeMData.hash
    parentId
    path
    latex
  }
  let mut result := #[current]
  if expr.getAppFn.isConstOf ``Exists then
    result := result.push {
      id := id ++ "/quantifier"
      kind := "quantifier-symbol"
      identity := "quantifier:" ++ id
      fingerprint := current.fingerprint
      parentId := some id
      path := path ++ ".quantifier"
      latex := "\\exists"
    }
  match expr with
  | .forallE binderName type body _ | .lam binderName type body _ =>
    let binderLatex := escapedBinderName binderName
    if !binderName.isAnonymous then
      if expr.isForall then
        result := result.push {
          id := id ++ "/quantifier"
          kind := "quantifier-symbol"
          identity := "quantifier:" ++ id
          fingerprint := toString type.consumeMData.hash
          parentId := some id
          path := path ++ ".quantifier"
          latex := "\\forall"
        }
      result := result.push {
        id := id ++ "/binder"
        kind := "declaration"
        identity := "binder:" ++ id
        fingerprint := toString type.consumeMData.hash
        parentId := some id
        path := path ++ ".binder"
        latex := binderLatex
      }
      result := result.push {
        id := id ++ "/binder-colon"
        kind := "declaration-punctuation"
        identity := "binder-colon:" ++ id
        fingerprint := toString type.consumeMData.hash
        parentId := some id
        path := path ++ ".binder.colon"
        latex := ":"
      }
    result := result ++ (← collectRawNodes rootId (path ++ ".0") (some id) type boundNames)
    result := result ++ (← collectRawNodes rootId (path ++ ".1") (some id) body
      (#[binderLatex] ++ boundNames))
  | .letE binderName type value body _ =>
    let binderLatex := escapedBinderName binderName
    result := result ++ (← collectRawNodes rootId (path ++ ".0") (some id) type boundNames)
    result := result ++ (← collectRawNodes rootId (path ++ ".1") (some id) value boundNames)
    result := result ++ (← collectRawNodes rootId (path ++ ".2") (some id) body
      (#[binderLatex] ++ boundNames))
  | _ =>
    for index in [0 : (exprChildren expr).size] do
      let child := (exprChildren expr)[index]!
      result := result ++ (← collectRawNodes rootId (path ++ "." ++ toString index)
        (some id) child boundNames)
  return result

private def occurrenceIndex (seen : List (String × Nat)) (key : String) : Nat :=
  (seen.find? (fun entry => entry.1 == key)).map (·.2) |>.getD 0

private def bumpOccurrence (seen : List (String × Nat)) (key : String) : List (String × Nat) :=
  let count := occurrenceIndex seen key
  (key, count + 1) :: seen.filter (fun entry => entry.1 != key)

/-- Render an expression once with LeanTeX, and attach every semantic occurrence
    to its exact character interval in that unchanged output. -/
def renderSemanticExpr (rootId : String) (expr : Expr) : MetaM (String × Array SemanticNode) := do
  let latex ← LeanTeX.run_latexPP expr {}
  let raw ← collectRawNodes rootId "0" none expr
  let mut seen : List (String × Nat) := []
  let mut nodes := #[]
  for node in raw do
    let occurrences := allOccurrences latex node.latex
    let occurrence := occurrenceIndex seen node.latex
    let spans := if h : occurrence < occurrences.size then #[occurrences[occurrence]] else #[]
    seen := bumpOccurrence seen node.latex
    nodes := nodes.push {
      id := node.id
      kind := node.kind
      identity := node.identity
      fingerprint := node.fingerprint
      parentId := node.parentId
      path := node.path
      latexSpans := spans
    }
  -- LeanTeX cannot render every partially applied internal expression in
  -- isolation. Once its visible children are located, their bounding interval
  -- is nevertheless the exact visible interval of that parent expression.
  -- Fill these spans bottom-up; this preserves punctuation such as `(x) ≤`
  -- across an `intro` instead of retaining only the visible leaf `f` and `0`.
  let mut childBounds : Std.HashMap String SemanticSpan := {}
  for reverseIndex in [0 : nodes.size] do
    let index := nodes.size - reverseIndex - 1
    let some node := nodes[index]? | continue
    let completed := if node.latexSpans.isEmpty then
      match childBounds[node.id]? with
      | some span => { node with latexSpans := #[span] }
      | none => node
    else node
    nodes := nodes.set! index completed
    if let some parentId := completed.parentId then
      for span in completed.latexSpans do
        let bounds := match childBounds[parentId]? with
          | some current => {
              start := min current.start span.start
              «end» := max current.«end» span.«end»
            }
          | none => span
        childBounds := childBounds.insert parentId bounds
  return (latex, nodes)

def shiftSemanticNodes (nodes : Array SemanticNode) (offset : Nat) : Array SemanticNode :=
  nodes.map fun node => { node with
    latexSpans := node.latexSpans.map fun span =>
      { start := span.start + offset, «end» := span.end + offset }
  }

private def commonPrefixLength (a b : String) : Nat := Id.run do
  let aa := a.toList
  let bb := b.toList
  let mut result := 0
  for index in [0 : min aa.length bb.length] do
    if aa[index]! != bb[index]! then return result
    result := result + 1
  return result

private def introBodyPathMatches (depth : Nat) (sourcePath targetPath : String) : Bool :=
  if depth == 0 || !targetPath.startsWith "0" then false
  else
    let binderPrefix := "0" ++ String.join (List.replicate depth ".1")
    sourcePath == binderPrefix ++ targetPath.drop 1

private def introBinderPathMatches (sourceDeclarations depth : Nat)
    (sourcePath targetPath sourceSuffix targetSuffix : String) : Bool := Id.run do
  for binderIndex in [0 : depth] do
    let sourcePrefix := "0" ++ String.join (List.replicate binderIndex ".1")
    let expectedSource := sourcePrefix ++ sourceSuffix
    let expectedTarget :=
      s!"context.{sourceDeclarations + binderIndex}{targetSuffix}"
    if sourcePath == expectedSource && targetPath == expectedTarget then
      return true
  return false

private def edgeReason (adapter : String) (sourceDeclarations introDepth : Nat)
    (source target : SemanticNode) : Option (String × Float) :=
  if adapter == "intro" && source.id.startsWith "target/" &&
      target.id.startsWith "target/" &&
      introBodyPathMatches introDepth source.path target.path &&
      ((source.kind == target.kind) || (source.kind == "bvar" && target.kind == "fvar")) then
    some (if source.kind == "bvar" then "verified-intro-binder-use"
      else "verified-intro-body", 1.0)
  else if adapter == "intro" && source.kind == "declaration" &&
      target.kind == "declaration" && source.id.startsWith "target/" &&
      target.id.startsWith "context/" && target.id.endsWith "/name" &&
      source.identity.startsWith "binder:" && source.fingerprint == target.fingerprint &&
      introBinderPathMatches sourceDeclarations introDepth source.path target.path
        ".binder" ".name" then
    some ("verified-intro-binder", 1.0)
  else if adapter == "intro" && source.kind == "declaration-punctuation" &&
      target.kind == "declaration-punctuation" && source.id.startsWith "target/" &&
      target.id.startsWith "context/" && target.id.endsWith "/colon" &&
      source.identity.startsWith "binder-colon:" &&
      source.fingerprint == target.fingerprint &&
      introBinderPathMatches sourceDeclarations introDepth source.path target.path
        ".binder.colon" ".colon" then
    some ("verified-intro-binder-punctuation", 1.0)
  else if source.kind != target.kind then none
  else if !source.identity.isEmpty && source.identity == target.identity then
    some (if source.kind == "fvar" then "same-fvar" else "same-identity", 1.0)
  else if source.fingerprint == target.fingerprint then
    some ("defeq-normal-form", 0.95)
  else if source.path == target.path && adapter == "rewrite" then
    some ("verified-rewrite-position", 0.90)
  else if source.path == target.path && adapter == "subst" then
    some ("verified-substitution-position", 0.90)
  else if source.path == target.path && adapter == "change" then
    some ("verified-definitional-change", 0.90)
  else none

/-- Deterministically pair expression occurrences.  Repeated symbols are
    disambiguated by their expression-tree context, never by screen position. -/
def semanticEdges (adapter : String) (source target : Array SemanticNode) : Array SemanticEdge := Id.run do
  let mut used : List String := []
  let mut usedTargets : List String := []
  let mut result := #[]
  let sourceDeclarations := source.countP fun node =>
    node.kind == "declaration" && node.id.startsWith "context/" &&
      node.id.endsWith "/name"
  let targetDeclarations := target.countP fun node =>
    node.kind == "declaration" && node.id.startsWith "context/" &&
      node.id.endsWith "/name"
  let introDepth := if adapter == "intro" then targetDeclarations - sourceDeclarations else 0
  -- Reserve exact quantifier occurrences before considering broader
  -- definitional matches.  A `have P` continuation can contain both a new
  -- hypothesis `P` and the unchanged target `P`; target-order matching would
  -- otherwise spend the old `∀` on the new hypothesis, then fade/recreate the
  -- genuinely persistent target quantifier at the same screen position.
  for targetNode in target do
    if targetNode.kind != "quantifier-symbol" || targetNode.identity.isEmpty then
      continue
    for sourceNode in source do
      if used.contains sourceNode.id then continue
      if sourceNode.kind == "quantifier-symbol" &&
          sourceNode.identity == targetNode.identity then
        used := sourceNode.id :: used
        usedTargets := targetNode.id :: usedTargets
        result := result.push {
          sourceNodeId := sourceNode.id
          targetNodeId := targetNode.id
          reason := "same-identity"
          confidence := 1.0
        }
        break
  for targetNode in target do
    if usedTargets.contains targetNode.id then continue
    let mut best : Option (Nat × Float × SemanticNode × String) := none
    for sourceNode in source do
      if used.contains sourceNode.id then continue
      let some (reason, confidence) :=
        edgeReason adapter sourceDeclarations introDepth sourceNode targetNode | continue
      let contextScore := commonPrefixLength sourceNode.path targetNode.path
      let candidate := (contextScore, confidence, sourceNode, reason)
      match best with
      | none => best := some candidate
      | some (bestContext, bestConfidence, _, _) =>
          if confidence > bestConfidence ||
              (confidence == bestConfidence && contextScore > bestContext) then
            best := some candidate
    if let some (_, confidence, sourceNode, reason) := best then
      used := sourceNode.id :: used
      result := result.push {
        sourceNodeId := sourceNode.id
        targetNodeId := targetNode.id
        reason
        confidence
      }
  return result

def tacticAdapter (text : String) : String :=
  let text := text.trimAscii
  if text.startsWith "rw" || text.startsWith "nth_rewrite" then "rewrite"
  else if text.startsWith "simp" then "simp"
  else if text.startsWith "subst" then "subst"
  else if text.startsWith "change" || text.startsWith "show" then "change"
  else if text.startsWith "calc" then "calc"
  else if text.startsWith "ring" then "ring"
  else if text.startsWith "linarith" || text.startsWith "nlinarith" then "linear-arithmetic"
  else if text.startsWith "cases" then "cases"
  else if text.startsWith "induction" then "induction"
  else if text.startsWith "constructor" then "constructor"
  else if text.startsWith "intro" || text.startsWith "rintro" then "intro"
  else "generic"

partial def collectConstants (expr : Expr) : Array String :=
  let own := match expr with
    | .const name _ => #[name.toString]
    | _ => #[]
  (exprChildren expr).foldl (fun result child => result ++ collectConstants child) own

partial def collectProofFVars (expr : Expr) : Array String :=
  let own := match expr with
    | .fvar id => #[id.name.toString]
    | _ => #[]
  (exprChildren expr).foldl
    (fun result child => result ++ collectProofFVars child) own

structure TacticExplanation where
  schemaVersion : Nat := 1
  adapter : String
  certificateKind : String
  certificateFingerprint : String
  /-- Exact free-variable identities referenced by the elaborated assignment. -/
  premiseIds : Array String := #[]
  /-- Constants occurring in the checked assignment. They are evidence, not a
      guessed rewrite order. Tactic-specific renderers may interpret them. -/
  supportingConstants : Array String := #[]
  expandable : Bool := false
deriving ToJson, FromJson, Repr

def tacticExplanation (text proofKind fingerprint : String)
    (premiseIds supportingConstants : Array String) : TacticExplanation :=
  let adapter := tacticAdapter text
  {
    adapter
    certificateKind := proofKind
    certificateFingerprint := fingerprint
    premiseIds := premiseIds.toList.eraseDups.toArray
    supportingConstants := supportingConstants.toList.eraseDups.toArray
    expandable := adapter ∈ ["rewrite", "simp", "ring", "linear-arithmetic"]
  }

def classifyProofAssignment : Option Expr → String
  | none => "unassigned"
  | some proof =>
    let constants := collectConstants proof
    if constants.any (·.startsWith "Eq.") then "equality-transport"
    else if constants.any (·.startsWith "Iff.") then "iff-transport"
    else if constants.any (fun name => (name.splitOn "congr").length > 1) then "congruence"
    else if proof.hasMVar then "goal-reduction"
    else "proof-term"

def proofFingerprint : Option Expr → String
  | none => ""
  | some proof => toString proof.consumeMData.hash

end Animate
