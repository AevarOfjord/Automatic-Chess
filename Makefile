# Dual-SCARA chess robot
#
#   make install   create venv (if needed) and install requirements.txt
#   make run       launch the visual simulator
#
# Make cannot leave a venv "activated" in your interactive shell. These targets
# call venv's python/pip directly, which is the reliable equivalent.

.PHONY: install run help

ifeq ($(OS),Windows_NT)
    VENV_DIR   := venv
    VENV_PY    := $(VENV_DIR)/Scripts/python.exe
    PYTHON_SYS := python
else
    VENV_DIR   := venv
    VENV_PY    := $(VENV_DIR)/bin/python
    PYTHON_SYS := python3
endif

help:
	@echo Targets:
	@echo   make install  - create ./venv if missing, then pip install -r requirements.txt
	@echo   make run      - ensure install, then launch: python -m chess_robot visual

# Create the virtual environment when the interpreter is missing.
$(VENV_PY):
	@echo Creating virtual environment in ./$(VENV_DIR) ...
	$(PYTHON_SYS) -m venv $(VENV_DIR)
	@echo Virtual environment ready.

# Install / refresh dependencies into the venv.
install: $(VENV_PY)
	@echo Installing dependencies from requirements.txt ...
	"$(VENV_PY)" -m pip install --upgrade pip
	"$(VENV_PY)" -m pip install -r requirements.txt
	@echo.
	@echo Install complete.
	@echo To activate this venv in your own terminal:
	@echo   PowerShell:  .\\venv\\Scripts\\Activate.ps1
	@echo   cmd.exe:     venv\\Scripts\\activate.bat
	@echo   bash:        source venv/bin/activate

# Launch the visual simulator (installs first so the venv is ready).
run: install
	@echo Launching visual simulator ...
	"$(VENV_PY)" -m chess_robot visual
