"""One generated function in a CPU/memory-bounded child with no file/network syscalls."""
import ast,ctypes,errno,json,resource,sys
name,source,tests=json.load(sys.stdin)
tree=ast.parse(source)
assert len(tree.body)==1 and isinstance(tree.body[0],ast.FunctionDef) and tree.body[0].name==name
allowed={ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.Name,ast.Load,ast.Store,ast.Constant,ast.Expr,ast.BinOp,ast.UnaryOp,ast.BoolOp,ast.Compare,ast.If,ast.IfExp,ast.For,ast.While,ast.Break,ast.Continue,ast.Pass,ast.Assign,ast.AugAssign,ast.Subscript,ast.Slice,ast.Call,ast.keyword,ast.Attribute,ast.List,ast.Tuple,ast.Set,ast.Dict,ast.ListComp,ast.GeneratorExp,ast.comprehension,ast.Add,ast.Sub,ast.Mult,ast.Mod,ast.FloorDiv,ast.Div,ast.Pow,ast.Gt,ast.GtE,ast.Lt,ast.LtE,ast.Eq,ast.NotEq,ast.And,ast.Or,ast.Not,ast.USub,ast.UAdd,ast.In,ast.NotIn,ast.Is,ast.IsNot}
for n in ast.walk(tree):
 if type(n) not in allowed or (isinstance(n,ast.Attribute) and n.attr not in ('count','append','reverse')) or (isinstance(n,ast.Name) and n.id.startswith('_')):print(json.dumps({'restricted':True,'passed':0}));sys.exit()
program=compile(tree,'<candidate>','exec')
lib=ctypes.CDLL('libseccomp.so.2')
lib.seccomp_init.argtypes=[ctypes.c_uint32];lib.seccomp_init.restype=ctypes.c_void_p
lib.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p]
lib.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
lib.seccomp_load.argtypes=[ctypes.c_void_p]
ctx=lib.seccomp_init(0x00050000|errno.EPERM)
for s in 'write exit exit_group brk mmap munmap mremap mprotect futex clock_gettime rt_sigreturn getpid gettid'.split():
 assert lib.seccomp_rule_add(ctx,0x7fff0000,lib.seccomp_syscall_resolve_name(s.encode()),0)==0
resource.setrlimit(resource.RLIMIT_CPU,(1,1));resource.setrlimit(resource.RLIMIT_AS,(192*2**20,192*2**20));assert lib.seccomp_load(ctx)==0
safe=dict(abs=abs,len=len,range=range,min=min,max=max,sum=sum,list=list,bool=bool,int=int,str=str,enumerate=enumerate,reversed=reversed,sorted=sorted,zip=zip,all=all,any=any)
g={'__builtins__':safe};exec(program,g);passed=0
for inp,want in tests:
 try:
  got=g[name](inp);passed+=int(type(got) is type(want) and got==want)
 except Exception:pass
print(json.dumps({'passed':passed,'restricted':False}))
