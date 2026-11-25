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
csc=r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
def compile(file,out,extra):
    files=[file] if isinstance(file,str) else list(file)
    subprocess.run([csc,'/nologo','/target:winexe','/platform:x64','/codepage:65001','/r:System.Windows.Forms.dll','/r:System.Drawing.dll','/r:System.Web.Extensions.dll','/r:System.Core.dll','/r:System.Management.dll','/r:Microsoft.CSharp.dll',*extra,'/out:'+str(out),*[str(base/f) for f in files]],check=True)
compile(['Desktop.cs','FigurePanel.cs','Theme.cs','SettingsForm.cs'],stage/'GovParse Desktop.exe',[])
release=base/'dist'
release.mkdir(exist_ok=True)
payload=base/'build/payload.zip'
with zipfile.ZipFile(payload,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
    for p in stage.rglob('*'):
        if p.is_file():z.write(p,p.relative_to(stage))
compile('Setup.cs',release/'GovParse-Desktop-Setup-x64.exe',['/r:System.IO.Compression.dll','/r:System.IO.Compression.FileSystem.dll','/r:Microsoft.CSharp.dll','/resource:'+str(payload)+',payload'])
shutil.copy2(stage/'manual.html',release/'사용설명서.html')
