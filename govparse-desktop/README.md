# GovParse Desktop

Windows x64 ships the C# WinForms application. The PySide6 interface in `gpqt/` runs from source on Windows for development and testing. It is intended for a separate macOS application bundle, with Linux planned later. Native macOS packaging and GUI verification are pending.

The desktop worker parses documents with rhwp. The JSON + PNG export uses GovParse for verified table images. HWPX export uses the app's IR and the vendored restorer in `gpdesk/vendor/hwp_restore.py` (engine commit `ea53268`). The separate MCP restorer has its own round-trip measurements.

OCR can discover a configured engine Python from a Windows LM Studio or Claude registration. For manual configuration, supply the executable and model paths as shown below.

## PySide6 application

Requirements: Python 3.12 and uv. The locked GUI environment currently targets Python 3.12.

From this directory:

    uv sync --locked --group dev
    uv run python -m gpqt.app

When MCP discovery is unavailable, place a local `config.json` next to this README with the engine paths for the current OS, for example:

    {"engine_python": "/path/to/python", "engine_src": "/path/to/document-parser-mcp/src", "ocr_models": "/path/to/rapidocr/models"}

OCR uses the model directory configured for the engine. Model weights are not included in this source checkout.

## GUI tests

The PySide6 tests exercise the application offscreen with a scripted worker and synthetic document data. They locate widgets by objectName and check interactions and state. They do not compare screenshot pixels.

From this directory:

    uv run pytest qt/tests -m "not integration" -q

Run the three real-worker tests with `uv run pytest qt/tests -m integration -q`. They open synthetic documents, export JSON and PNG, and reopen results after moving the files. To inspect the screen, start the app and open `samples/synthetic-figures.hwpx`.

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
