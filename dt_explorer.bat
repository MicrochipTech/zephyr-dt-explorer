@echo off
rem Start the Devicetree Explorer (opens http://localhost:8765 in the browser)
rem Optional argument: path to the zephyrproject folder
py "%~dp0dt_explorer.py" %*
