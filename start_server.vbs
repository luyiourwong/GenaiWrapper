Option Explicit

Dim objShell, scriptPath, command

' 取得腳本所在目錄
scriptPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' 建立命令：使用 pythonw 在背景執行 uvicorn
' pythonw 不會顯示控制台視窗
command = "pythonw -m uvicorn genaiwrapper.main:app --host 127.0.0.1 --port 8000 --log-level info"

' 執行命令
Set objShell = CreateObject("WScript.Shell")
objShell.Run command, 0, False

Set objShell = Nothing
