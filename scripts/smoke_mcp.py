"""Exercise the actual stdio MCP and Java PDF path using generated input only.

Author: chatgpt_gpt6. No original documents, Hancom conversion, or OCR models used.
"""
import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tempfile

import pymupdf
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(java_home=None):
    with tempfile.TemporaryDirectory(prefix='document-parser-mcp-smoke-') as directory:
        root=Path(directory); source=root/'input'; source.mkdir()
        pdf=source/'synthetic.pdf'
        with pymupdf.open() as document:
            page=document.new_page(width=500,height=500)
            page.insert_text((50,50),'Synthetic document parser MCP smoke input',fontsize=14)
            for x in (50,220,390): page.draw_line((x,100),(x,220))
            for y in (100,160,220): page.draw_line((50,y),(390,y))
            for x,y,text in [(65,135,'Name'),(235,135,'Value'),(65,195,'Alpha'),(235,195,'42')]: page.insert_text((x,y),text,fontsize=12)
            document.save(pdf)
        before=hashlib.sha256(pdf.read_bytes()).hexdigest()
        data=root/'data'
        env={**os.environ,'GOVPARSE_DATA':str(data),'GOVPARSE_HEAP':'1g','PYTHONUTF8':'1'}
        env.pop('PYTHONPATH',None)
        if java_home: env['JAVA_HOME']=java_home
        calls=[]
        params=StdioServerParameters(command=sys.executable,args=['-m','govparse.mcp_server'],env=env)
        async with stdio_client(params) as (reader,writer):
            async with ClientSession(reader,writer) as session:
                init=await session.initialize()
                names=sorted(t.name for t in (await session.list_tools()).tools)
                assert len(names)==18,names

                async def call(name,arguments):
                    result=await session.call_tool(name,arguments,read_timeout_seconds=__import__('datetime').timedelta(seconds=180))
                    assert not result.isError,(name,result)
                    structured=result.structuredContent or {}
                    text=structured.get('result') or '\n'.join(b.text for b in result.content if b.type=='text')
                    calls.append(name)
                    return text

                doctor=await call('doctor',{})
                assert 'OK   java' in doctor,doctor
                prepared=await call('prepare_corpus',{'source_dir':str(source)})
                manifest=data/'05_reports/manifest.csv'
                rows=list(csv.DictReader(manifest.open(encoding='utf-8-sig')))
                docs=[r for r in rows if r['normalized_name'].endswith('.pdf')]
                assert len(docs)==1,(prepared,rows)
                name=docs[0]['normalized_name']
                listed=await call('list_documents',{})
                assert name in listed,listed
                parsed=await call('parse_document',{'doc_name':name,'output_format':'json,html','whole_file':True})
                assert '성공 1 / 실패 0' in parsed,parsed
                tables=json.loads(await call('get_tables',{'doc_name':name,'page':1,'output_format':'json','recover_missing':True}))
                assert not tables.get('error'),tables
                assert 'Alpha' in json.dumps(tables) and '42' in json.dumps(tables),tables
                visual=json.loads(await call('get_visual_evidence',{'doc_name':name,'page':1,'dpi':100}))
                assert not visual.get('error'),visual
                rag=json.loads(await call('export_rag_chunks',{'doc_name':name}))
                assert not rag.get('error'),rag
                assert Path(rag['jsonl_path']).is_file(),rag
                chunks=[json.loads(line) for line in Path(rag['jsonl_path']).read_text(encoding='utf-8').splitlines()]
                assert chunks,chunks
                assert hashlib.sha256(pdf.read_bytes()).hexdigest()==before
                return {'author':'chatgpt_gpt6','date':'2026-10-16','package_version':importlib.metadata.version('govparse'),
                        'module_path':str(__import__('govparse.mcp_server',fromlist=['main']).__file__),
                        'source_kind':'PyMuPDF-generated one-page PDF, no original source document','source_sha256':before,
                        'tool_count':len(names),'tool_names':names,'completed_calls':calls,
                        'synthetic_pdf_parse':'one successful Java/ODL unit; Alpha and 42 found in returned table JSON',
                        'rag_records':len(chunks),'source_unchanged':True,
                        'not_executed':['real HWP/HWPX native parsing','Hancom COM conversion','real OCR model inference','arbitrary document quality evaluation'],
                        'temporary_inputs_outputs':'Generated in temporary directory and removed at completion'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java-home')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    result=asyncio.run(run(args.java_home))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
