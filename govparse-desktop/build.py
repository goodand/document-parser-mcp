from pathlib import Path
import shutil, subprocess, sys, zipfile, importlib.metadata as md
base=Path(__file__).resolve().parent
stage=base/'build'/'app'
runtime=stage/'runtime'
site=runtime/'Lib'/'site-packages'
shutil.rmtree(site,ignore_errors=True)  # no stale duplicate package versions
site.mkdir(parents=True,exist_ok=True)
py=Path(sys.base_prefix)
def tree(src,dst):
    shutil.copytree(src,dst,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc','.git'))
for name in ('python.exe','python312.dll','python3.dll','vcruntime140.dll','vcruntime140_1.dll'):
    shutil.copy2(py/name,runtime/name)
tree(py/'DLLs',runtime/'DLLs')
for path in (py/'Lib').iterdir():
    if path.name in ('site-packages','test','idlelib','tkinter','turtledemo','ensurepip','__pycache__'): continue
    if path.is_dir(): tree(path,runtime/'Lib'/path.name)
    elif path.suffix=='.py': shutil.copy2(path,runtime/'Lib'/path.name)
(runtime/'python312._pth').write_text('.\nLib\nDLLs\nLib/site-packages\n','ascii')
licenses=stage/'licenses'
licenses.mkdir(exist_ok=True)
shutil.copy2(py/'LICENSE.txt',licenses/'CPython.txt')
for name in ('rhwp-python','pydantic','pydantic-core','annotated-types','typing-extensions','typing-inspection','lxml','pymupdf','olefile'):
    dist=md.distribution(name)
    for file in dist.files:
        if '..' in file.parts or '__pycache__' in file.parts or file.suffix=='.pyc': continue
        src=Path(dist.locate_file(file))
        if not src.is_file():continue
        dest=site/file
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dest)
        if 'license' in src.name.lower() or 'copying' in src.name.lower():
            shutil.copy2(src,licenses/(name+'-'+src.name))
for name in ('worker.py','manual.html'):
    shutil.copy2(base/name,stage/name)
# engine code is used only through the pinned copy in gpdesk/vendor (see PROVENANCE.txt)
# app modules used by worker.py (figure review, linkage, HTML captions)
shutil.rmtree(site/'gpdesk',ignore_errors=True)
shutil.copytree(base/'gpdesk',site/'gpdesk',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
# local override (engine_python / engine_src / ocr_models), read by gpdesk.config from next to the package; not in the repository
if '--local-config' in sys.argv and (base/'config.json').is_file(): shutil.copy2(base/'config.json',site/'config.json')
# OCR inside the app, so it reads pictures on a PC that has no govparse engine:
# RapidOCR and its dependencies, the engine's OCR module (same function as MCP ocr_image) and the models.
sys.path.insert(0,str(base))
from gpdesk import config as _config
_cfg=_config.load()
_engine_site=_cfg.engine_python.parents[1]/'Lib'/'site-packages'
def _dist(name):
    try: return md.distribution(name)
    except md.PackageNotFoundError: return next(iter(md.distributions(name=name,path=[str(_engine_site)])))
for name in ('rapidocr','onnxruntime','opencv-python-headless','numpy','pyclipper','shapely','pillow','pyyaml','omegaconf','tqdm','six','colorlog','colorama','flatbuffers','protobuf','packaging','requests','urllib3','idna','certifi','charset-normalizer'):
    dist=_dist(name)
    for file in dist.files:
        if '..' in file.parts or '__pycache__' in file.parts or file.suffix=='.pyc': continue
        if file.suffix.lower() in ('.onnx','.ort'): continue   # weights come only from OCR_MODELS below (RapidOCR defaults, onnxruntime samples excluded)
        src=Path(dist.locate_file(file))
        if not src.is_file():continue
        dest=site/file
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(src,dest)
        if 'license' in src.name.lower() or 'copying' in src.name.lower():
            shutil.copy2(src,licenses/(name+'-'+src.name))
for name in ('msvcp140.dll','msvcp140_1.dll'):  # onnxruntime needs the C++ runtime; a clean PC may not have it
    dll=Path(r'C:\Windows\System32')/name
    if dll.is_file(): shutil.copy2(dll,runtime/name)
engine_package = base.parent / 'src' / 'govparse'
if not engine_package.is_dir():
    engine_package = _cfg.engine_src / 'govparse'
for module in ('image_ocr.py', 'table_selection.py'):
    if not (engine_package / module).is_file():
        raise SystemExit(f'engine module missing from the desktop build: {engine_package / module}')
shutil.rmtree(site/'govparse',ignore_errors=True)
shutil.copytree(engine_package,site/'govparse',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
subprocess.run([str(runtime/'python.exe'), '-I', '-c',
                'import cv2; from govparse.table_selection import extract_selected_table_images'], check=True)
if not _cfg.ocr_models: raise SystemExit('RapidOCR model folder not found; set ocr_models in config.json')
# The exact weights shipped: name -> SHA-256 from RapidOCR 3.10.0 default_models.yaml (its official download list).
OCR_MODELS={
    'ch_PP-OCRv5_det_mobile.onnx':('4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae','onnx/PP-OCRv5/det'),
    'korean_PP-OCRv5_rec_mobile.onnx':('cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b','onnx/PP-OCRv5/rec'),
    'ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx':('54379ae5174d026780215fc748a7f31910dee36818e63d49e17dc598ecc82df7','onnx/PP-OCRv5/cls'),
}
import hashlib
shutil.rmtree(stage/'models',ignore_errors=True)
(stage/'models'/'rapidocr').mkdir(parents=True)
notice=['# 동봉한 OCR 모델 (Bundled OCR model weights)','',
        'All three are PaddleOCR PP-OCRv5 models converted to ONNX by RapidOCR, Apache-2.0',
        '(see rapidocr-MODEL_LICENSES.md and rapidocr-LICENSE in this folder). Unmodified.','',
        '| file | SHA-256 | source | license |','|---|---|---|---|']
for name,(sha,sub) in OCR_MODELS.items():
    src=Path(_cfg.ocr_models)/name
    got=hashlib.sha256(src.read_bytes()).hexdigest()
    if got!=sha: raise SystemExit(f'OCR model {name}: SHA-256 {got} is not the RapidOCR 3.10.0 release file')
    shutil.copy2(src,stage/'models'/'rapidocr'/name)
    notice.append(f'| {name} | {sha} | https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/{sub}/{name} | Apache-2.0 (PaddleOCR / RapidOCR Authors) |')
(licenses/'OCR-MODELS.md').write_text(chr(10).join(notice)+chr(10),'utf-8')
# What actually ships: exactly these weights and nothing else.
shipped=sorted(p.relative_to(stage).as_posix() for p in stage.rglob('*') if p.suffix.lower() in ('.onnx','.ort'))
expected=sorted('models/rapidocr/'+n for n in OCR_MODELS)
if shipped!=expected: raise SystemExit('unexpected model files in the app: '+', '.join(sorted(set(shipped)^set(expected))))
print('shipped models:',', '.join(shipped))
csc=r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
def compile(file,out,extra):
    files=[file] if isinstance(file,str) else list(file)
    subprocess.run([csc,'/nologo','/target:winexe','/platform:x64','/codepage:65001','/r:System.Windows.Forms.dll','/r:System.Drawing.dll','/r:System.Web.Extensions.dll','/r:System.Core.dll','/r:System.Management.dll','/r:Microsoft.CSharp.dll',*extra,'/out:'+str(out),*[str(base/f) for f in files]],check=True)
compile(['Desktop.cs','FigurePanel.cs','Theme.cs','SettingsForm.cs','TablePanel.cs'],stage/'GovParse Desktop.exe',[])
release=base/'dist'
release.mkdir(exist_ok=True)
payload=base/'build/payload.zip'
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
    for p in stage.rglob('*'):
        if p.is_file():z.write(p,p.relative_to(stage))
compile('Setup.cs',release/'GovParse-Desktop-Setup-x64.exe',['/r:System.IO.Compression.dll','/r:System.IO.Compression.FileSystem.dll','/r:Microsoft.CSharp.dll','/resource:'+str(payload)+',payload'])
shutil.copy2(stage/'manual.html',release/'사용설명서.html')
