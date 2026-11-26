import sys,collections,subprocess
O=sys.argv[1]
maps=[]
for l in open(O+"/maps"):
    p=l.split(None,5)
    a,b=(int(x,16) for x in p[0].split("-"))
    maps.append((a,b,int(p[2],16),p[5].strip() if len(p)>5 else ""))
c=collections.Counter()
for l in open(O+"/samples"):
    if l[0]=="#": continue
    pc=int(l.split()[1],16)
    for a,b,off,path in maps:
        if a<=pc<b and path.endswith("libc.so.6"):
            c[(path,pc-a+off)]+=1
for (path,off),n in c.most_common(6):
    print("== off 0x%x n=%d"%(off,n))
    r=subprocess.run(["objdump","-d","--start-address=0x%x"%(off-0x18),"--stop-address=0x%x"%(off+0x8),path],capture_output=True,text=True)
    print("\n".join(x for x in r.stdout.splitlines() if ":\t" in x))
