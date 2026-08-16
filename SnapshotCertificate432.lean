import Animate

open Lean

namespace SnapshotCertificate432

private def atomicWrite (path : System.FilePath) (json : Json) : IO Unit := do
  if let some parent := path.parent then
    IO.FS.createDirAll parent
  let temporary := path.withExtension (path.extension.getD "json" ++ ".writing")
  IO.FS.writeFile temporary (json.pretty (lineWidth := 200))
  IO.FS.rename temporary path

elab "#proof_video_certificate " theoremId:ident output:str sourceSha:str : command => do
  let theoremName := theoremId.getId
  let outputPath : System.FilePath := ⟨output.getString⟩
  let sourceSha256 := sourceSha.getString
  Elab.Command.runTermElabM fun _ => do
    let order ← ProofTrace.sourceCurrentModuleProofOrder theoremName
    let mut rows := #[]
    for name in order do
      let (proofFingerprint, axioms, validation) ←
        Animate.certifyHybridChapter name
      let dependencies ← ProofTrace.sourceCurrentModuleProofDependencies name
      rows := rows.push ({
        theoremName := name.toString
        dependencies := dependencies.map (·.toString)
        proofFingerprint
        axioms
        validation
      } : Animate.SnapshotCertificateRow)
    let bundle : Animate.SnapshotCertificateBundle := {
      selectedTheorem := theoremName.toString
      sourceSha256
      rows
    }
    atomicWrite outputPath (toJson bundle)
    unless bundle.rows.back?.any (·.theoremName == theoremName.toString) do
      throwError "snapshot certificate closure does not end in {theoremName}"
    unless bundle.rows.all (·.validation.valid) do
      let failures := bundle.rows.filter (!·.validation.valid) |>.map fun row =>
        s!"{row.theoremName}: {String.intercalate "; " row.validation.errors.toList}"
      throwError "snapshot certificate contains an invalid kernel chapter: {String.intercalate " | " failures.toList}"

end SnapshotCertificate432
