"""Generic census experiment rules. Author: chatggpt_gpt6."""
import re
DOTS='·‧․ㆍ·・･'
def projection(text):
 return re.sub(r'[\s\x01'+DOTS+']','',text).casefold()
def match_name(name,text):
 return bool(projection(name)) and projection(name) in projection(text)
def extend_toc(rows):
 """Only dense numbered tab/page runs; single records are never sufficient."""
 out=[dict(x) for x in rows];run=[]
 eligible=set();seen={}
 for item in out:
  section=item['anchor']['section'];text=item['text'].strip()
  if text:
   seen[section]=seen.get(section,0)+1
   if seen[section]<=20 and re.fullmatch(r'[-—–]*(목차|차례|순서|contents)[-—–]*',re.sub(r'\s','',text).casefold()):eligible.add(section)
 def flush():
  if len(run)>=5 and all(run[k][1]<=run[k+1][1] for k in range(len(run)-1)):
   for idx,_ in run:
    if out[idx]['anchor']['section'] in eligible:out[idx].update(exclude_from_chunking=True,toc_status='heading_and_numbered_monotonic_page_run')
    elif not out[idx]['exclude_from_chunking']:out[idx]['toc_status']='hold_unseeded_numbered_page_run'
  run.clear()
 previous=None
 for i,p in enumerate(out):
  section=p['anchor']['section']
  if section!=previous:flush()
  previous=section;t=p['text'].strip()
  m=re.fullmatch(r'\(\d+\)\s*[^\t\n]*[가-힣A-Za-z][^\t\n]*\t+(\d{1,5})',t)
  if m:run.append((i,int(m[1])))
  elif t:flush()
 flush();return out

def cell_segments(text):
 """Preserve text; segment only explicit line-initial list markers."""
 starts=[m.start() for m in re.finditer(r'(?m)^\s*[○․•]\s*',text)]
 if not starts:return [{'start':0,'end':len(text),'text':text,'explicit_item':False}]
 edges=sorted(set([0]+starts+[len(text)]))
 return [{'start':a,'end':z,'text':text[a:z],'explicit_item':bool(re.match(r'\s*[○․•]',text[a:z]))} for a,z in zip(edges,edges[1:]) if text[a:z].strip()]
