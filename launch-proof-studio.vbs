Option Explicit
Dim shell, fso, projectRoot, executable, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)
executable = fso.BuildPath(projectRoot, ".venv\Scripts\python.exe")
If Not fso.FileExists(executable) Then
  MsgBox "Proof Studio is not installed. Run setup.ps1 once.", 16, "Lean Proof Studio"
  WScript.Quit 1
End If
command = Chr(34) & executable & Chr(34) & " -m proof_video.studio.launcher start --root " & Chr(34) & projectRoot & Chr(34)
shell.Run command, 0, False
