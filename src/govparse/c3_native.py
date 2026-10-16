# -*- coding: utf-8 -*-
"""Minimal HWP 5.0 BodyText record reader (paragraph text + table structure)."""
import sys, zlib, struct, olefile, io
import hashlib


TAG_BEGIN=0x10
T_PARA_HEADER=TAG_BEGIN+50   # 66
T_PARA_TEXT  =TAG_BEGIN+51   # 67
T_PARA_CHAR_SHAPE=TAG_BEGIN+52
T_PARA_LINE_SEG=TAG_BEGIN+53
T_CTRL_HEADER=TAG_BEGIN+55   # 71
T_LIST_HEADER=TAG_BEGIN+56   # 72
T_TABLE      =TAG_BEGIN+61   # 77
T_SHAPE_COMPONENT=TAG_BEGIN+60

CHAR_CTRL={0,10,13,24,25,26,27,28,29,30,31}
INLINE_CTRL={4,5,6,7,8,9,19,20}
EXT_CTRL={1,2,3,11,12,14,15,16,17,18,21,22,23}

def records(buf):
    i=0; n=len(buf)
    while i+4<=n:
        h=struct.unpack_from("<I",buf,i)[0]; i+=4
        tag=h & 0x3FF; lvl=(h>>10)&0x3FF; size=(h>>20)&0xFFF
        if size==0xFFF:
            size=struct.unpack_from("<I",buf,i)[0]; i+=4
        data=buf[i:i+size]; i+=size
        yield tag,lvl,size,data

def para_text(data):
    """returns (plain_text, ctrl_list) ; ctrl_list of (pos, code, ctrlid)"""
    out=[]; ctrls=[]
    j=0; n=len(data)
    while j+2<=n:
        c=struct.unpack_from("<H",data,j)[0]
        if c in CHAR_CTRL:
            if c==13: out.append("\n")
            elif c==10: out.append("\n")
            elif c==9: out.append("\t")
            else: out.append("")
            ctrls.append((len(out),c,None)); j+=2
        elif c in INLINE_CTRL or c in EXT_CTRL:
            blk=data[j:j+16]
            cid=blk[2:6]
            try: cids=cid[::-1].decode("ascii")
            except Exception: cids=repr(cid)
            ctrls.append((len(out),c,cids))
            if c==9: out.append("\t")
            j+=16
        else:
            out.append(chr(c)); j+=2
    return "".join(out), ctrls

def load(path):
    f=olefile.OleFileIO(path)
    hdr=f.openstream("FileHeader").read()
    flags=struct.unpack_from("<I",hdr,36)[0]
    compressed=bool(flags & 1)
    secs=[]
    for e in f.listdir():
        if e[0]=="BodyText":
            secs.append(e)
    secs.sort(key=lambda e:int(e[1].replace("Section","")))
    out=[]
    for e in secs:
        raw=f.openstream(e).read()
        if compressed: raw=zlib.decompress(raw,-15)
        out.append((e[1],raw))
    return f,compressed,out


def native(path):
    f, compressed, sections = load(path)
    output = {}
    for name, raw in sections:
        rr = list(records(raw))
        offset = 0
        recs, paras = [], []
        current = None
        for n, (tag, level, size, payload) in enumerate(rr):
            raw_header = struct.unpack_from('<I', raw, offset)[0]
            header_size = 8 if raw_header >> 20 == 0xfff else 4
            assert offset + header_size + size <= len(raw)
            item = {'rec': n, 'tag': tag, 'level': level, 'offset': offset,
                    'payload_offset': offset+header_size, 'size': size,
                    'payload_sha256': hashlib.sha256(payload).hexdigest()}
            if tag == T_PARA_HEADER:
                current = {'para_index': len(paras), 'header_rec': n, 'level': level,
                           'text_records': [], 'text': '', 'controls': []}
                paras.append(current)
            if tag == T_PARA_TEXT:
                text, controls = para_text(payload)
                item.update(text=text, controls=controls)
                if current is not None and current['level'] + 1 == level:
                    current['text'] += text
                    current['text_records'].append(n)
                    current['controls'].extend(controls)
                    item['para_index'] = current['para_index']
            if tag == T_TABLE:
                item['rows'], item['cols'] = struct.unpack_from('<HH', payload, 4)
            recs.append(item)
            offset += header_size + size
        assert offset == len(raw)
        tables = []
        for n, (tag, level, size, payload) in enumerate(rr):
            if tag != T_TABLE:
                continue
            cells, cell = [], None
            stop = n+1
            nested = []
            for j in range(n+1, len(rr)):
                tg, lv, sz, dd = rr[j]
                if lv < level:
                    stop = j
                    break
                if tg == T_LIST_HEADER and lv == level and len(dd) >= 34:
                    col, row, colspan, rowspan = struct.unpack_from('<HHHH', dd, 8)
                    cell = {'row': row, 'col': col, 'rowspan': rowspan, 'colspan': colspan,
                            'list_header_rec': j, 'paragraphs': [], 'nested_tables': []}
                    cells.append(cell)
                elif tg == T_TABLE and lv > level:
                    nested.append(j)
                    if cell is not None:
                        cell['nested_tables'].append(j)
                elif tg == T_PARA_TEXT and lv == level+1 and cell is not None:
                    cell['paragraphs'].append({'rec': j, 'para_index': recs[j].get('para_index'),
                                               'text': recs[j]['text']})
            tables.append({'table_rec': n, 'level': level, 'rows': recs[n]['rows'],
                           'cols': recs[n]['cols'], 'end_rec_exclusive': stop,
                           'cells': cells, 'nested_tables': nested})
        output[name] = {'record_count': len(rr), 'paragraph_count': len(paras),
                        'records': recs, 'paragraphs': paras, 'tables': tables}
    f.close()
    return output
