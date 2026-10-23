"""Codemod: stop async functions from blocking the event loop.
  * exact:  `<builder>.execute()` inside async def -> `(await aexec(<builder>))`
  * exact:  resolved call to a sync helper that (transitively) hits the DB -> `(await offload(helper, args))`
  * whole-body: async def with no await that does blocking work -> sync def + @to_async (or plain def for routes/deps)
Usage: asyncify.py [--apply] ; prints a report either way."""
import ast, pathlib, sys, re, collections
APPLY="--apply" in sys.argv
ROOT=pathlib.Path("app")
ROUTE={"get","post","put","patch","delete","head","options"}
SKIP_DECOS={"exception_handler","on_event","middleware"}

def modname(p): 
    parts=list(p.with_suffix("").parts)
    if parts[-1]=="__init__": parts=parts[:-1]
    return ".".join(parts)
files={modname(p):p for p in ROOT.rglob("*.py")}
trees={m:ast.parse(p.read_bytes()) for m,p in files.items()}
def _wrapped(n): return any(isinstance(d,ast.Name) and d.id=="to_async" for d in n.decorator_list)
top={m:{n.name:n for n in t.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))} for m,t in trees.items()}

def imports(m):
    """local name -> ('func',mod,name) | ('mod',mod)"""
    out={}
    pkg=m.rsplit(".",1)[0] if not files[m].name=="__init__.py" else m
    for n in trees[m].body:
        for n in ast.walk(n) if isinstance(n,(ast.Try,ast.If)) else [n]:
            if isinstance(n,ast.ImportFrom):
                base=n.module or ""
                if n.level:
                    parts=pkg.split(".")
                    parts=parts[:len(parts)-(n.level-1)]
                    base=".".join(parts+([n.module] if n.module else []))
                for a in n.names:
                    loc=a.asname or a.name
                    if f"{base}.{a.name}" in files: out[loc]=("mod",f"{base}.{a.name}")
                    elif base in files: out[loc]=("func",base,a.name)
            elif isinstance(n,ast.Import):
                for a in n.names:
                    if a.name in files: out[a.asname or a.name.split(".")[0]]=("mod",a.name) if a.asname else None
    return {k:v for k,v in out.items() if v}
IMP={m:imports(m) for m in files}

def locals_of(fn):
    s={a.arg for a in fn.args.args+fn.args.kwonlyargs+fn.args.posonlyargs}
    if fn.args.vararg: s.add(fn.args.vararg.arg)
    if fn.args.kwarg: s.add(fn.args.kwarg.arg)
    for n in ast.walk(fn):
        if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store): s.add(n.id)
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n is not fn: s.add(n.name)
        if isinstance(n,(ast.Import,ast.ImportFrom)):
            for a in n.names: s.add((a.asname or a.name).split(".")[0])
    return s

def resolve(m,call,loc):
    f=call.func
    if isinstance(f,ast.Name) and f.id not in loc:
        if f.id in top[m]: return (m,f.id)
        imp=IMP[m].get(f.id)
        if imp and imp[0]=="func" and imp[2] in top[imp[1]]: return (imp[1],imp[2])
    if isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name) and f.value.id not in loc:
        imp=IMP[m].get(f.value.id)
        if imp and imp[0]=="mod" and f.attr in top[imp[1]]: return (imp[1],f.attr)
    return None

def is_exec(n): return isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=="execute" and not n.args and not n.keywords

# blocking sync top-level functions (transitive, precise resolution)
blocking=set()
for m,d in top.items():
    for name,fn in d.items():
        if isinstance(fn,ast.FunctionDef) and not _wrapped(fn) and any(is_exec(n) for n in ast.walk(fn)): blocking.add((m,name))
ch=True
while ch:
    ch=False
    for m,d in top.items():
        for name,fn in d.items():
            if (m,name) in blocking or not isinstance(fn,ast.FunctionDef) or _wrapped(fn): continue
            loc=locals_of(fn)
            if any(isinstance(n,ast.Call) and resolve(m,n,loc) in blocking for n in ast.walk(fn)):
                blocking.add((m,name)); ch=True

def own(fn):
    stack=[(c,None) for c in fn.body]
    while stack:
        n,par=stack.pop(); yield n,par
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.Lambda,ast.ClassDef)): continue
        stack.extend((c,n) for c in ast.iter_child_nodes(n))

def parents_map(fn):
    pm={}
    for n,_ in own(fn):
        for c in ast.iter_child_nodes(n): pm[c]=n
    return pm
def in_genexp(n,pm):
    while n in pm:
        n=pm[n]
        if isinstance(n,(ast.GeneratorExp,ast.SetComp,ast.DictComp)) : return True  # await in these: make async gens/comps; be conservative
    return False
def awaited(n,pm): return isinstance(pm.get(n),ast.Await)

# awaiting callers (by bare name/attr) to decide def vs to_async for routes/deps
await_called=set()
for t in trees.values():
    for n in ast.walk(t):
        if isinstance(n,ast.Await) and isinstance(n.value,ast.Call):
            f=n.value.func; await_called.add(f.id if isinstance(f,ast.Name) else getattr(f,"attr",None))
for p in list(pathlib.Path("tests").rglob("*.py"))+[pathlib.Path("../test_saas.py")]:
    if p.exists():
        for n in ast.walk(ast.parse(p.read_bytes())):
            if isinstance(n,ast.Await) and isinstance(n.value,ast.Call):
                f=n.value.func; await_called.add(f.id if isinstance(f,ast.Name) else getattr(f,"attr",None))

report=collections.defaultdict(list); manual=[]; stats=collections.Counter()
for m,p in sorted(files.items()):
    src=p.read_bytes(); tree=trees[m]
    lines=src.split(b"\n"); starts=[0]
    for l in lines: starts.append(starts[-1]+len(l)+1)
    off=lambda ln,col: starts[ln-1]+col
    edits=[]; need=set()
    for fn in [n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef)]:
        if any(isinstance(n,(ast.Yield,ast.YieldFrom)) for n,_ in own(fn)): continue
        decos=[dd for d in fn.decorator_list for dd in [d.func if isinstance(d,ast.Call) else d] if isinstance(dd,ast.Attribute)]
        if any(d.attr in SKIP_DECOS for d in decos): continue
        is_route=any(d.attr in ROUTE for d in decos)
        loc=locals_of(fn); pm=parents_map(fn)
        nodes=[n for n,_ in own(fn)]
        has_await=any(isinstance(n,(ast.Await,ast.AsyncFor,ast.AsyncWith)) for n in nodes)
        execs=[n for n in nodes if is_exec(n)]
        helpers=[n for n in nodes if isinstance(n,ast.Call) and not awaited(n,pm) and resolve(m,n,loc) in blocking]
        if not execs and not helpers: continue
        stats["async_blocking"]+=1
        ident=f"{p}:{fn.lineno} {fn.name}"
        if not has_await:
            # whole-body conversion
            is_dep=fn.name in {"require_admin","require_service_key","require_platform_admin","require_tenant"}
            if (is_route or is_dep) and fn.name not in await_called:
                kind="def"
            else:
                kind="to_async"
            if is_route and kind!="def": kind="to_async_route"  # route with awaiting callers: keep async wrapper
            ln=fn.lineno; col=fn.col_offset
            seg=src[off(ln,col):off(ln,col)+9]
            assert seg.startswith(b"async def"),(ident,seg)
            if kind=="def": rep=b"def"
            else: rep=b"@to_async\n"+b" "*col+b"def"; need.add("to_async")
            edits.append((off(ln,col),off(ln,col)+9,rep))
            stats[kind]+=1; report[str(p)].append((fn.name,kind)); continue
        # with awaits: per-call rewrite
        for n in execs:
            if in_genexp(n,pm): manual.append((ident,n.lineno,"execute in comprehension")); continue
            if awaited(n,pm): continue
            b=n.func.value
            s,e=off(b.lineno,b.col_offset),off(b.end_lineno,b.end_col_offset)
            edits.append((off(n.lineno,n.col_offset),off(n.end_lineno,n.end_col_offset),b"(await aexec("+src[s:e]+b"))"))
            need.add("aexec"); stats["execute"]+=1
        for n in helpers:
            if in_genexp(n,pm): manual.append((ident,n.lineno,"helper in comprehension")); continue
            fs,fe=off(n.func.lineno,n.func.col_offset),off(n.func.end_lineno,n.func.end_col_offset)
            ce=off(n.end_lineno,n.end_col_offset)
            inner=src[fe:ce].strip(); assert inner[:1]==b"(" and inner[-1:]==b")",(ident,inner[:30])
            inner=inner[1:-1].strip().rstrip(b",")
            body=src[fs:fe]+((b", "+inner) if inner else b"")
            edits.append((off(n.lineno,n.col_offset),ce,b"(await offload("+body+b"))")); need.add("offload"); stats["helper"]+=1
        report[str(p)].append((fn.name,"per-call"))
    if not edits: continue
    edits.sort(key=lambda e:e[0]); final=[]; last_end=-1
    for e in edits:
        if e[0]<last_end: manual.append((str(p),None,"overlapping edit dropped")); continue
        final.append(e); last_end=e[1]
    out=bytearray(src)
    for st,en,rep in reversed(final): out[st:en]=rep
    # import line
    names=sorted(need)
    imp=("from app.services.aio import "+", ".join(names)+"\n").encode()
    if b"app.services.aio import" not in out and m!="app.services.aio":
        t2=ast.parse(bytes(out)); ins=None
        for n in t2.body:
            if isinstance(n,(ast.Import,ast.ImportFrom)) and not (isinstance(n,ast.ImportFrom) and n.module=="__future__"):
                ins=n.lineno; break
        if ins is None:
            ins=1
            for n in t2.body:
                if isinstance(n,ast.Expr) and isinstance(getattr(n,"value",None),ast.Constant): ins=n.end_lineno+1
                elif isinstance(n,ast.ImportFrom) and n.module=="__future__": ins=n.end_lineno+1
                else: break
        ol=bytes(out).split(b"\n"); ol.insert(ins-1,imp.rstrip(b"\n")); out=bytearray(b"\n".join(ol))
    ast.parse(bytes(out))  # must still be valid python
    if APPLY: p.write_bytes(bytes(out))
    stats["files_touched"]+=1
print(dict(stats)); 
for k,v in sorted(report.items()): print(k,len(v))
print("manual:",manual)
