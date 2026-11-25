using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Windows.Forms;
using System.Diagnostics;
class Setup {
    [STAThread] static int Main(string[] args){
        bool test=args.Length==2 && args[0]=="--extract";
        string path=test?Path.GetFullPath(args[1]):Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"GovParse Desktop","0.1.0");
        try{
            if(!test && MessageBox.Show("GovParse Desktop을 설치합니다.\n"+path,"설치",MessageBoxButtons.OKCancel)!=DialogResult.OK)return 0;
            if(!Directory.Exists(path)){
                string temp=path+"-"+Guid.NewGuid().ToString("N");Directory.CreateDirectory(temp);
                try{using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("payload"))
                    using(var archive=new ZipArchive(stream,ZipArchiveMode.Read)){archive.ExtractToDirectory(temp);}
                    Directory.Move(temp,path);
                }finally{if(Directory.Exists(temp))Directory.Delete(temp,true);}
            }else if(test || !File.Exists(Path.Combine(path,"GovParse Desktop.exe")))throw new IOException("설치 위치가 이미 존재합니다.");
            if(!test){
                string menu=Environment.GetFolderPath(Environment.SpecialFolder.Programs);
                dynamic shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
                dynamic link=shell.CreateShortcut(Path.Combine(menu,"GovParse Desktop.lnk"));
                link.TargetPath=Path.Combine(path,"GovParse Desktop.exe");link.WorkingDirectory=path;link.Save();
                if(MessageBox.Show("설치가 완료되었습니다. 지금 실행할까요?","GovParse Desktop",MessageBoxButtons.YesNo)==DialogResult.Yes)Process.Start(link.TargetPath);
            }return 0;
        }catch(Exception e){if(!test)MessageBox.Show(e.Message,"설치 실패");return 1;}
    }
}
