@echo off
echo Starting GenaiWrapper...
echo.
echo Press Ctrl+C to stop the server.
echo.

python -m uvicorn genaiwrapper.main:app --host 127.0.0.1 --port 8000 --log-level info
