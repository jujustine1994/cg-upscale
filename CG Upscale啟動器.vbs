' CG Upscale launcher (double-click entry, no console window)
' Runs launcher.ps1 -Silent hidden; launcher.ps1 starts the app with pythonw.exe.
' First install / missing components: launcher.ps1 reopens itself in a normal window.
Option Explicit
Dim shell, fso, project, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
project = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = project
command = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & _
          Chr(34) & project & "\launcher.ps1" & Chr(34) & " -Silent"
shell.Run command, 0, False
