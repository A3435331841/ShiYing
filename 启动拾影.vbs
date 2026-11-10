Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
shell.Run """" & folder & "\.venv\Scripts\pythonw.exe"" """ & folder & "\launch_shiying.py""", 0, False
