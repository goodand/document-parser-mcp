"""Native paragraph TOC exclusion, author chatggpt_gpt6.
HWP record decoding adapted from tmp/claude_silver/hwp.py; never rewrites source.
"""
import argparse
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path
from zipfile import ZipFile, is_zipfile

from .toc_filter import HEADING, LEADER

ENTRY = re.compile(r"^[^\t\n]*[가-힣A-Za-z][^\t\n]*\t+\s*\d{1,5}\s*$")
GROUP = re.compile(r"^[\[<〈【].{1,60}[\]>〉】]$")

VOLUME = re.compile(r"^[\[(<〈【]?\s*\d+\s*권\s*[\])>〉】]?$")
SEPARATOR = re.compile(r"^[\s—–-]{2,}$")
PAGELESS = re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX]+[.]\s*[^\t\n]{1,60}\t\s*$")


def detect_format(path):
    """Validate container structure, never infer the parser from the suffix."""
    import olefile
    if olefile.isOleFile(str(path)):
        with olefile.OleFileIO(path) as ole:
            if ole.exists("FileHeader") and ole.openstream("FileHeader").read(32).startswith(b"HWP Document File"):
                return "hwp"
        raise ValueError("OLE container is not an HWP document")
    if is_zipfile(path):
        with ZipFile(path) as archive:
            if any(re.fullmatch(r"Contents/section\d+\.xml", n) for n in archive.namelist()):
                return "hwpx"
        raise ValueError("ZIP container has no HWPX sections")
    raise ValueError("Unsupported document container")


def _entry(item):
    text = item["text"].strip()
    return bool(ENTRY.fullmatch(text) or LEADER.fullmatch(text) or
                (item.get("page_crossref") and re.search(r"\d\s*$", text)))


def _bridge_end(rows, start, section):
    """A few short headings between dense entry runs; no prose/table rows."""
    headings = entries = 0
    for j in range(start, min(len(rows), start + 40)):
        item = rows[j]
        if item["anchor"]["section"] != section:
            return None
        text = item["text"].strip()
        if _entry(item):
            entries += 1
            if entries == 3:
                return j
        elif not text or SEPARATOR.fullmatch(text):
            continue
        elif entries:
            return None
        elif (len(re.sub(r"\s", "", text)) <= 16 and
              re.fullmatch(r"[가-힣A-Za-zⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX0-9 ,· .()<>\[\]\t]+", text) and
              not re.search(r"[.!?]$|다$", text) and "\n" not in text and "\t" not in text.rstrip("\t")):
            headings += 1
            if headings > 3:
                return None
        else:
            return None
    return None


def classify_paragraphs(paragraphs):
    out = [dict(p, exclude_from_chunking=False, toc_status="keep") for p in paragraphs]
    for i, p in enumerate(out):
        if not HEADING.fullmatch(re.sub(r"\s", "", p["text"])):
            continue
        end, entries, last_entry, gap = i + 1, 0, i, 0
        section = p["anchor"]["section"]
        while end < len(out) and out[end]["anchor"]["section"] == section:
            item = out[end]
            text = item["text"].strip()
            if _entry(item):
                entries += 1
                last_entry = end
                gap = 0
            elif (not text or GROUP.fullmatch(text) or VOLUME.fullmatch(text)
                  or SEPARATOR.fullmatch(text) or PAGELESS.fullmatch(item["text"].rstrip("\n"))
                  or HEADING.fullmatch(re.sub(r"\s", "", text))) and gap < 12:
                gap += 1
            else:
                bridge = _bridge_end(out, end, section) if entries >= 3 else None
                if bridge is None:
                    break
                entries += 3
                last_entry = bridge
                end = bridge
                gap = 0
            end += 1
        if entries >= 3:
            for item in out[i:last_entry+1]:
                item.update(exclude_from_chunking=True, toc_status="heading_seeded_entry_run")
        else:
            p["toc_status"] = "hold_insufficient_entries"
    return out


def read_hwp(path):
    import olefile
    out = []
    with olefile.OleFileIO(path) as ole:
        flags = struct.unpack_from("<I", ole.openstream("FileHeader").read(), 36)[0]
        if flags & (2 | 4):
            raise ValueError("Encrypted/distribution HWP unsupported")
        names = sorted((s for s in ole.listdir() if len(s)==2 and s[0]=="BodyText" and re.fullmatch(r"Section\d+",s[1])), key=lambda s:int(s[1][7:]))
        if not names:
            raise ValueError("No native HWP BodyText sections")
        for name in names:
            data = ole.openstream(name).read()
            if flags & 1:
                data = zlib.decompress(data, -15)
            pos = rec = 0
            stack = []
            ordinal = 0
            while pos < len(data):
                if pos+4 > len(data):
                    raise ValueError("Truncated record header")
                h = struct.unpack_from("<I", data, pos)[0]; pos += 4
                tag, level, size = h & 1023, (h>>10)&1023, h>>20
                if size == 4095:
                    size = struct.unpack_from("<I",data,pos)[0]; pos += 4
                payload = data[pos:pos+size]; pos += size
                if len(payload) != size:
                    raise ValueError("Truncated record")
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if tag == 66:
                    p = {"anchor":{"section":"/".join(name),"ordinal":ordinal,"paragraph_header_rec":rec}, "text":""}
                    out.append(p);stack.append((level,p));ordinal += 1
                elif tag == 67 and stack:
                    pieces=[];j=0
                    while j < len(payload):
                        code=struct.unpack_from("<H",payload,j)[0]
                        if code < 32:
                            pieces.append({9:"\t",10:"\n",13:"\n"}.get(code,""))
                            j += 16 if code in {1,2,3,4,5,6,7,8,9,11,12,14,15,16,17,18,19,20,21,22,23} else 2
                        else:
                            start=j
                            while j+2<=len(payload) and struct.unpack_from("<H",payload,j)[0]>=32:
                                j+=2
                            pieces.append(payload[start:j].decode("utf-16le",errors="surrogatepass"))
                    stack[-1][1]["text"] += "".join(pieces)
                rec += 1
    return out


def read_hwpx(path):
    from lxml import etree
    out=[]
    with ZipFile(path) as archive:
        names=sorted((n for n in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml",n)),key=lambda s:int(re.search(r"section(\d+)",s)[1]))
        if not names:
            raise ValueError("No HWPX sections")
        for name in names:
            root=etree.fromstring(archive.read(name),etree.XMLParser(resolve_entities=False,no_network=True))
            paragraphs=root.xpath('.//*[local-name()="p"]')
            for ordinal,p in enumerate(paragraphs):
                pieces=[];cross=False
                for node in p.iterdescendants():
                    owner=next((a for a in node.iterancestors() if etree.QName(a).localname=="p"),None)
                    if owner is not p:
                        continue
                    kind=etree.QName(node).localname
                    if kind=="t":
                        pieces.append(node.text or "")
                    elif kind=="tab":
                        pieces.append("\t")
                    elif kind=="lineBreak":
                        pieces.append("\n")
                    if kind=="fieldBegin" and node.get("type")=="CROSSREF":
                        values={v.get("name"):v.text for v in node.xpath('./*[local-name()="parameters"]/*')}
                        cross |= values.get("RefContentType")=="OBJECT_TYPE_PAGE"
                    if kind in {"tab","lineBreak"}:
                        pieces.append(node.tail or "")
                out.append({"anchor":{"section":name,"ordinal":ordinal,"xpath":root.getroottree().getpath(p)},"text":"".join(pieces),"page_crossref":bool(cross)})
    return out


def run(source, output):
    source,output=Path(source).resolve(),Path(output).resolve()
    if output.exists():
        raise FileExistsError("Use a new output directory")
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    actual_format=detect_format(source)
    reader={"hwp":read_hwp,"hwpx":read_hwpx}[actual_format]
    if reader is None:
        raise ValueError("Expected HWP or HWPX")
    rows=classify_paragraphs(reader(source))
    output.mkdir(parents=True)
    for name,items in [("paragraphs.jsonl",rows),("body.jsonl",[p for p in rows if not p["exclude_from_chunking"]]),("toc.jsonl",[p for p in rows if p["exclude_from_chunking"]])]:
        with (output/name).open("w",encoding="utf-8") as f:
            for p in items:
                f.write(json.dumps(p,ensure_ascii=True)+"\n")
    manifest={"author":"chatggpt_gpt6","source":str(source),"sha256":before,"actual_format":actual_format,"suffix_mismatch":source.suffix.lower() != "."+actual_format,"paragraphs":len(rows),"excluded":sum(p["exclude_from_chunking"] for p in rows),"source_unchanged":before==hashlib.sha256(source.read_bytes()).hexdigest(),"unit":"native_paragraph_not_page","limitations":["No source document rewrite","No rendered page boundaries","Unheaded/irregular TOC may remain","Body JSONL is a text view; original objects remain in source"]}
    (output/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return manifest


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source");parser.add_argument("output")
    args=parser.parse_args();print(json.dumps(run(args.source,args.output),ensure_ascii=False))
