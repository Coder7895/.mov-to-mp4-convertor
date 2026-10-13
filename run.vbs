Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strPath = fso.GetParentFolderName(WScript.ScriptFullName)

' Set environment PATH to include MSYS2 ucrt64 tools
Set oEnv = WshShell.Environment("PROCESS")
oEnv("PATH") = "C:\msys64\ucrt64\bin;" & oEnv("PATH")

pythonExe = "python.exe"
If fso.FileExists("C:\msys64\ucrt64\bin\python.exe") Then
    pythonExe = "C:\msys64\ucrt64\bin\python.exe"
End If

WshShell.CurrentDirectory = strPath
WshShell.Run """" & pythonExe & """ app.py", 0, False
