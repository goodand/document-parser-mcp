# GovParse Desktop

GovParse Desktop is a document review and export application. The shared PySide6 interface runs on Windows, macOS, and Linux. Windows-specific WinForms files and the installer source remain available for the existing Windows build.

The app uses the GovParse MCP engine for document parsing and restoration. The engine is installed separately. The app can find an engine registered in LM Studio or Claude. For a checkout of the combined repository, it also finds the engine source in the sibling src/ directory.

## PySide6 application

Requirements: Python 3.12 and uv. The locked GUI environment currently targets Python 3.12.

From this directory:

    uv sync --locked --group dev
    uv run python -m gpqt.app

Set the engine Python and engine source in the user-local config.json only when discovery from the MCP client configuration is unavailable. OCR uses the model directory configured for the engine. Model weights are not included in this source checkout.

## GUI tests

The PySide6 tests exercise the application offscreen with a scripted worker and synthetic document data. They locate widgets by objectName and check interactions and state. They do not compare screenshot pixels.

From this directory:

    uv run pytest qt/tests -m "not integration" -q

The four integration tests use the real worker and synthetic fixtures. They require the GovParse source checkout and its runtime dependencies. Set QT_QPA_PLATFORM=offscreen to run the GUI tests without a display. For a visible manual check, start the app and open samples/synthetic-figures.hwpx.

## Windows compatibility app

Desktop.cs, FigurePanel.cs, Theme.cs, and SettingsForm.cs implement the existing WinForms build. On Windows x64 with the .NET Framework C# compiler and the local OCR model dependencies configured, build it with:

    uv run python build.py

The installer and bundled runtime are generated under dist/. They are build outputs and are not included in the source repository.

## Project layout

- gpqt/ contains the cross-platform PySide6 interface.
- gpdesk/ contains the document worker, exports, figure review, and platform-neutral processing.
- worker.py starts the document worker.
- qt/ contains the GUI behavior specification and pytest-qt tests.
- tests/ contains worker and export tests.
- samples/ contains generated synthetic fixtures and their generators.

Real source documents, credentials, local configuration, compiled installers, and model weights do not belong in this repository.
