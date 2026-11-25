# GovParse Desktop

A Windows desktop front end for the GovParse MCP engine. The app creates JSON and HTML exports, reviews embedded pictures, captures rendered tables, and restores editable HWPX. Saving as HWP uses Hancom Office when available.

## Requirements

- Windows x64 and Python 3.12 for development
- Hancom Office for HWP conversion and GUI verification
- GovParse MCP engine, installed separately

The engine is not bundled. Set its Python and source paths in a user-local config.json or let the app discover GovParse from LM Studio or Claude configuration. See manual.html for user instructions.

## Development

Install the locked environment with uv:

    uv sync --locked --python 3.12
    uv run pytest tests -m "not live"

Live integration tests require a configured engine. HWP export and GUI verification also require Hancom Office.

Build the Windows installer from an activated environment with:

    uv run python build.py

The installer and bundled runtime are generated under dist/. Build on Windows x64 with the .NET Framework C# compiler available at the Windows path used by build.py.

Synthetic fixtures and their generator scripts are used for regression tests. Real source documents and OCR model weights are not included.
