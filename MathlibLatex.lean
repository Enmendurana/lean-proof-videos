import LeanTeX
import Mathlib

/-!
# Central mathematical LaTeX dictionary

This is the single source of truth for turning elaborated Lean/Mathlib
constants into notation intended for mathematicians. The general Mathlib
rules are adapted from `kmill/LeanTeX-mathlib` (Apache-2.0, upstream commit
`02f8d141abf202f91ca634c0cac82c1a819a3095`) and extended for proof videos.

Exact registered printers always take priority. The final generic printers
only remove implementation namespaces and record an intelligible operator
name; they never change the underlying Lean expression or proof semantics.
-/

open Lean LeanTeX

namespace ProofVideo.MathlibLatex

/-! ## Core types and set notation -/

latex_pp_const_rule Nat :=
  (LatexData.atomString "\\mathbb{N}").maybeWithTooltip "Nat"

latex_pp_const_rule Int :=
  (LatexData.atomString "\\mathbb{Z}").maybeWithTooltip "Int"

latex_pp_const_rule Rat :=
  (LatexData.atomString "\\mathbb{Q}").maybeWithTooltip "Rat"

latex_pp_const_rule Real :=
  (LatexData.atomString "\\mathbb{R}").maybeWithTooltip "Real"

latex_pp_const_rule Real.pi :=
  (LatexData.atomString "\\pi").maybeWithTooltip "Real.pi"

latex_pp_app_rules (const := Set)
  | _, #[α] => do
    let α ← latexPP α
    return (LatexData.atomString "\\mathcal{P}").sub α

latex_pp_app_rules (const := Finset)
  | _, #[α] => do
    let α ← latexPP α
    return (LatexData.atomString "\\mathcal{P}_{\\mathrm{fin}}").sub α

latex_pp_app_rules (const := HasSubset.Subset)
  | _, #[_, _, a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return a.protectRight 50 ++ LatexData.nonAssocOp " \\subseteq " 50 ++ b.protectLeft 50

@[latex_pp_app const.Union.union]
def ppUnion := basicBinOpPrinter " \\cup " 65 .left 4

@[latex_pp_app const.Inter.inter]
def ppInter := basicBinOpPrinter " \\cap " 70 .left 4

latex_pp_app_rules (const := Set.image)
  | _, #[_, _, f, X] => do
    let f ← latexPP f
    let X ← latexPP X
    return f.protectRight funAppBP ++ X.brackets
      |>.mergeBP (lbp := .NonAssoc funAppBP) (rbp := .NonAssoc funAppBP)

latex_pp_app_rules (const := Singleton.singleton)
  | _, #[_, _, _, a] => do
    let a ← latexPP a
    return "\\{ " ++ a ++ " \\}" |>.resetBP .Infinity .Infinity

latex_pp_app_rules (const := setOf)
  | _, #[_, predicate] =>
    withBindingBodyUnusedName' predicate `x fun name body => do
      let body ← latexPP body
      return ("\\left\\{ " ++ name.toLatex ++ " \\mid " ++ body ++
        " \\right\\}") |>.resetBP

/-! ## Big operators, intervals, and elementary functions -/

latex_pp_app_rules (const := Finset.univ)
  | _, #[ty, _] => latexPP ty

latex_pp_app_rules (const := Finset.prod)
  | _, #[_α, _β, _inst, s, f] => do
    let set ← withExtraSmallness 2 <| latexPP s
    withBindingBodyUnusedName' f `i fun name body => do
      let body ← latexPP body
      let op := (LatexData.atomString "\\prod" |>.bigger 1).sub
        (s!"{name.toLatex} \\in " ++ set)
      return (op ++ body.protectLeft 66).resetBP

latex_pp_app_rules (const := Finset.sum)
  | _, #[_α, _β, _inst, s, f] => do
    let set ← withExtraSmallness 2 <| latexPP s
    withBindingBodyUnusedName' f `i fun name body => do
      let body ← latexPP body
      let op := (LatexData.atomString "\\sum" |>.bigger 1).sub
        (s!"{name.toLatex} \\in " ++ set)
      return (op ++ body.protectLeft 66).resetBP

private def closedInterval (left right : String) (lo hi : Expr) : LatexPrinterM LatexData := do
  let lo ← latexPP lo
  let hi ← latexPP hi
  return (left ++ lo ++ ", " ++ hi ++ right).resetBP .Infinity .Infinity

private def leftInfiniteInterval (right : String) (hi : Expr) : LatexPrinterM LatexData := do
  let hi ← latexPP hi
  return ("(-\\infty, " ++ hi ++ right).resetBP .Infinity .Infinity

private def rightInfiniteInterval (left : String) (lo : Expr) : LatexPrinterM LatexData := do
  let lo ← latexPP lo
  return (left ++ lo ++ ", \\infty)").resetBP .Infinity .Infinity

latex_pp_app_rules (const := Finset.Icc)
  | _, #[_, _, _, lo, hi] => closedInterval "[" "]" lo hi
latex_pp_app_rules (const := Finset.Ico)
  | _, #[_, _, _, lo, hi] => closedInterval "[" ")" lo hi
latex_pp_app_rules (const := Finset.Ioc)
  | _, #[_, _, _, lo, hi] => closedInterval "(" "]" lo hi
latex_pp_app_rules (const := Finset.Ioo)
  | _, #[_, _, _, lo, hi] => closedInterval "(" ")" lo hi
latex_pp_app_rules (const := Finset.Iic)
  | _, #[_, _, _, hi] => leftInfiniteInterval "]" hi
latex_pp_app_rules (const := Finset.Iio)
  | _, #[_, _, _, hi] => leftInfiniteInterval ")" hi
latex_pp_app_rules (const := Finset.Ici)
  | _, #[_, _, _, lo] => rightInfiniteInterval "[" lo
latex_pp_app_rules (const := Finset.Ioi)
  | _, #[_, _, _, lo] => rightInfiniteInterval "(" lo
latex_pp_app_rules (const := Set.Icc)
  | _, #[_, _, lo, hi] => closedInterval "[" "]" lo hi
latex_pp_app_rules (const := Set.Ico)
  | _, #[_, _, lo, hi] => closedInterval "[" ")" lo hi
latex_pp_app_rules (const := Set.Ioc)
  | _, #[_, _, lo, hi] => closedInterval "(" "]" lo hi
latex_pp_app_rules (const := Set.Ioo)
  | _, #[_, _, lo, hi] => closedInterval "(" ")" lo hi
latex_pp_app_rules (const := Set.Iic)
  | _, #[_, _, hi] => leftInfiniteInterval "]" hi
latex_pp_app_rules (const := Set.Iio)
  | _, #[_, _, hi] => leftInfiniteInterval ")" hi
latex_pp_app_rules (const := Set.Ici)
  | _, #[_, _, lo] => rightInfiniteInterval "[" lo
latex_pp_app_rules (const := Set.Ioi)
  | _, #[_, _, lo] => rightInfiniteInterval "(" lo

latex_pp_app_rules (const := Finset.range)
  | _, #[hi] => do
    let hi ← latexPP hi
    return ("[0, " ++ hi ++ ")").resetBP .Infinity .Infinity

latex_pp_app_rules (const := Nat.ceil)
  | _, #[_, _, _, e] => do
    let e ← latexPP e
    return ("\\left\\lceil " ++ e ++ " \\right\\rceil").resetBP

latex_pp_app_rules (const := Nat.floor)
  | _, #[_, _, _, e] => do
    let e ← latexPP e
    return ("\\left\\lfloor " ++ e ++ " \\right\\rfloor").resetBP

latex_pp_app_rules (const := Real.sqrt)
  | _, #[x] => do
    let x ← latexPP x
    return LatexData.atomString s!"\\sqrt\{{x.latex.1}}"

macro "proof_video_trig_rule" c:ident tex:str : command =>
  `(latex_pp_app_rules (const := $c)
      | _, #[x] => do
        let x ← latexPP x
        return LatexData.atomString $tex ++ " " ++ x.protect (funAppBP - 1))

proof_video_trig_rule Real.sin "\\sin"
proof_video_trig_rule Real.cos "\\cos"
proof_video_trig_rule Real.tan "\\tan"
proof_video_trig_rule Real.arcsin "\\sin^{-1}"
proof_video_trig_rule Real.arccos "\\cos^{-1}"
proof_video_trig_rule Real.arctan "\\tan^{-1}"

/-! ## Suppressed implementation details and conventional operations -/

latex_pp_app_rules (const := Nat.cast)
  | _, #[_, _, n] => latexPP n

latex_pp_app_rules (const := Int.cast)
  | _, #[_, _, n] => latexPP n

latex_pp_app_rules (const := Min.min)
  | _, #[_, _, a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return ("\\min\\left(" ++ a ++ ", " ++ b ++ "\\right)").resetBP

latex_pp_app_rules (const := Max.max)
  | _, #[_, _, a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return ("\\max\\left(" ++ a ++ ", " ++ b ++ "\\right)").resetBP

latex_pp_app_rules (const := And)
  | _, #[a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return a.protectRight 35 ++ LatexData.rightAssocOp " \\land " 35 ++ b.protectLeft 35

latex_pp_app_rules (const := Or)
  | _, #[a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return a.protectRight 30 ++ LatexData.rightAssocOp " \\lor " 30 ++ b.protectLeft 30

latex_pp_app_rules (const := Dvd.dvd)
  | _, #[_, _, a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return a.protectRight 50 ++ LatexData.nonAssocOp " \\mid " 50 ++ b.protectLeft 50

latex_pp_app_rules (const := Inv.inv)
  | _, #[_, _, a] => do
    let a ← latexPP a
    return a.protectRight 100 ++ LatexData.atomString "^{-1}"

latex_pp_app_rules (const := HSMul.hSMul)
  | _, #[_, _, _, _, a, b] => do
    let a ← latexPP a
    let b ← latexPP b
    return a.protectRight 70 ++ LatexData.leftAssocOp " \\cdot " 70 ++ b.protectLeft 70

latex_pp_app_rules (const := DirectSum)
  | _, #[ι, β, _inst] => do
    let ι ← withExtraSmallness 2 <| latexPP ι
    withBindingBodyUnusedName' β `i fun name body => do
      let body ← latexPP body
      let op := (LatexData.atomString "\\bigoplus" |>.bigger 1).sub
        (s!"{name.toLatex} \\in " ++ ι)
      return (op ++ body).resetBP

latex_pp_app_rules (const := TensorProduct)
  | _, #[R, _, M, N, _, _, _, _] => do
    let R ← latexPP R
    let M ← latexPP M
    let N ← latexPP N
    return M.protectRight 100 ++ (LatexData.atomString "\\otimes").sub R ++ N.protectLeft 100

latex_pp_app_rules (const := PiTensorProduct)
  | _, #[ι, _R, _inst, modules, _, _] => do
    let ι ← withExtraSmallness 2 <| latexPP ι
    withBindingBodyUnusedName' modules `i fun name body => do
      let body ← latexPP body
      let operator := (LatexData.atomString "\\bigotimes" |>.bigger 1).sub
        (s!"{name.toLatex} \\in " ++ ι)
      return (operator ++ body).resetBP

/-! ## Declarative proof-video vocabulary

These entries are names rather than compile-time references, so they also
cover declarations defined later in an input file. This is what lets one
central dictionary format `Erdos38.lean` without making that file import a
renderer-specific module.
-/

private def constantSymbol? (name : Name) : Option String :=
  match name.toString with
  | "ShiftApproxData" => some "\\mathcal{D}_{\\mathrm{shift}}"
  | "HMul.hMul" | "Mul.mul" => some "\\cdot"
  | "HAdd.hAdd" | "Add.add" => some "+"
  | "HSub.hSub" | "Sub.sub" => some "-"
  | "LE.le" => some "\\le"
  | "LT.lt" => some "<"
  | "GE.ge" => some "\\ge"
  | "GT.gt" => some ">"
  | _ => none

private def explicitArguments (args : Array Expr) (kinds : Array ParamKind) : Array Expr := Id.run do
  let mut result := #[]
  for arg in args, kind in kinds do
    if kind.bInfo.isExplicit then
      result := result.push arg
  return result

private def unaryOperator (operator : String) (arg : Expr) : LatexPrinterM LatexData := do
  let arg ← latexPP arg
  return LatexData.atomString operator ++ arg.parens

private def binaryFunction (operator : String) (a b : Expr) : LatexPrinterM LatexData := do
  let a ← latexPP a
  let b ← latexPP b
  return LatexData.atomString operator ++ (LatexData.intercalate ", " #[a, b]).parens

private def indexedFunction (operator : String) (index : Expr)
    (arguments : Array Expr) : LatexPrinterM LatexData := do
  let index ← latexPP index
  let arguments ← arguments.mapM latexPP
  return (LatexData.atomString operator).sub index ++
    (LatexData.intercalate ", " arguments).parens

private def firstIsomorphismTheorem (φ : Expr) : LatexPrinterM LatexData := do
  let φLatex ← latexPP φ
  let φType ← Meta.whnf (← Meta.inferType φ)
  let domain := φType.getAppArgs[0]?
  let domainLatex ← match domain with
    | some type => latexPP type
    | none => pure <| LatexData.atomString "\\operatorname{dom}" ++ φLatex.parens
  return (domainLatex ++ " / " ++ (LatexData.atomString "\\ker" ++ φLatex.parens) ++
    " \\cong " ++ (LatexData.atomString "\\operatorname{im}" ++ φLatex.parens)).resetBP

private def erdosApplication? (name : Name) (args : Array Expr) : LatexPrinterM LatexData := do
  match name.toString, args with
  | "schnirelmannDensity", #[A] => unaryOperator "\\sigma" A
  | "erdos_f", #[α] => unaryOperator "f" α
  | "constructB", #[d] => do
      let d ← latexPP d
      return (LatexData.atomString "B").sub d
  | "IsAdditiveBasis", #[B] => unaryOperator "\\mathsf{AddBasis}" B
  | "countIn", #[A, N] => do
      let A ← latexPP A
      let N ← latexPP N
      return ("\\left|" ++ A ++ " \\cap [1," ++ N ++ "]\\right|").resetBP
  | "translateSet", #[A, b] => do
      let A ← latexPP A
      let b ← latexPP b
      return A.protectRight 65 ++ LatexData.leftAssocOp "+" 65 ++ b.protectLeft 65
  | "unionTranslateCount", #[A, b, N] => do
      let A ← latexPP A
      let b ← latexPP b
      let N ← latexPP N
      return ("\\left|\\left(" ++ A ++ " \\cup (" ++ A ++ "+" ++ b ++
        ")\\right) \\cap [1," ++ N ++ "]\\right|").resetBP
  | "hSumset", #[h, B] => do
      let h ← latexPP h
      let B ← latexPP B
      return (LatexData.atomString "\\Sigma").sub h ++ B.parens
  | "hitCount", #[A, C, s] => do
      let A ← latexPP A
      let C ← latexPP C
      let s ← latexPP s
      return ("\\left|(" ++ A ++ "+" ++ s ++ ") \\cap " ++ C ++ "\\right|").resetBP
  | "shiftL", #[m] => do
      let m ← latexPP m
      return (LatexData.atomString "L").sub m
  | "omegaPrim", #[M] => do
      let M ← latexPP M
      return (LatexData.atomString "\\omega").sub M
  | "shiftBilinForm", #[a, E, C, d] =>
      indexedFunction "\\mathcal{B}" d #[a, E, C]
  | "maxPolyOnGrid", #[a, d, M, _hM] => do
      let d ← latexPP d
      let M ← latexPP M
      let a ← latexPP a
      return (LatexData.atomString "\\mathcal{M}").sub
        (LatexData.intercalate "," #[d, M]) ++ a.parens
  | "ShiftApproxData.shifts", #[d, m] => do
      let m ← latexPP m
      let d ← latexPP d
      return (LatexData.atomString "S").sub m ++ d.parens
  | "Set.Nonempty", #[S] => do
      let S ← latexPP S
      return S.protectRight 50 ++ LatexData.nonAssocOp " \\ne " 50 ++
        LatexData.atomString "\\varnothing"
  | "QuotientGroup.quotientKerEquivRange", #[φ]
  | "QuotientAddGroup.quotientKerEquivRange", #[φ] =>
      firstIsomorphismTheorem φ
  | "MonoidHom.ker", #[φ]
  | "AddMonoidHom.ker", #[φ]
  | "LinearMap.ker", #[φ] => unaryOperator "\\ker" φ
  | "MonoidHom.range", #[φ]
  | "AddMonoidHom.range", #[φ]
  | "LinearMap.range", #[φ] => unaryOperator "\\operatorname{im}" φ
  | _, _ => failure

private def escapeOperatorName (name : Name) : String :=
  let short := (name.toString.splitOn ".").getLastD name.toString
  short.replace "_" "\\_"

latex_pp_rules (kind := const)
  | .const name _ =>
    match constantSymbol? name with
    | some symbol => return LatexData.atomString symbol
    | none => failure

latex_pp_app_rules (kind := any) (paramKinds := kinds)
  | f, args => do
    let some name := f.constName? | failure
    let explicit := explicitArguments args kinds
    try
      erdosApplication? name explicit
    catch _ =>
      let rendered ← explicit.mapM latexPP
      let operator := "\\operatorname{" ++ escapeOperatorName name ++ "}"
      return LatexData.atomString operator ++
        (LatexData.intercalate ", " rendered).parens

end ProofVideo.MathlibLatex
