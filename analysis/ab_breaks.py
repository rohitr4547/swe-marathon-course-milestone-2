"""For each whole-app run: A or B checks that failed after having passed once C, D or E work began, and the
regression-suite results the agent saw before and after that point. Usage: python3 analysis/ab_breaks.py"""
import csv,json,glob,re
CMD=re.compile(r"```mswea_bash_command\n(.*?)```",re.S)
CHK=re.compile(r"(check_([a-e])_\d+\.sh|regression\.sh)")
RES=re.compile(r"RESULT: (\d+) passed, (\d+) failed"); REG=re.compile(r"REGRESSION: (\d+) broken")
for r in csv.DictReader(open("analysis/runs.tsv"),delimiter="\t"):
    if not r["run"].startswith("5"): continue
    m=json.load(open(glob.glob(f"tasks/slack-clone-course/jobs/{r['job']}/*/agent/mini-swe-agent.trajectory.json")[0]))["messages"]
    state={}; cur=None; events=[]; regs=[]; started_cde=False
    for i,x in enumerate(m):
        if x["role"]!="assistant" or i+1>=len(m): continue
        c="\n".join(CMD.findall(str(x["content"])))
        called=[t for t in CHK.findall(c) if re.search(r"bash\s+\S*"+re.escape(t[0]),c)]
        if not called: continue
        out=str(m[i+1]["content"]); res=RES.findall(out); reg=REG.findall(out)
        for name,comp in called:
            if comp and comp in "cde": cur=comp.upper(); started_cde=True
            if name=="regression.sh":
                if reg: n=int(reg.pop(0)); regs.append((n,started_cde)); ok=(n==0); key="regression"
                else: continue
            else:
                if not res: continue
                p,f=map(int,res.pop(0)); ok=(f==0); key=name
            if comp in ("a","b") or key=="regression":
                if state.get(key)==True and not ok and started_cde: events.append((key,cur))
                if key=="regression" and regs and len(regs)>1 and started_cde and regs[-1][0]>regs[-2][0]: events.append((f"suite {regs[-2][0]}->{regs[-1][0]}",cur))
                state[key]= ok if not (key in state and state[key]==True and ok) else True
    print(r["run"],r["repeat"],"breaks:",events or "none","| suite before C-E:",[n for n,b in regs if not b][-6:],"after:",[n for n,b in regs if b])
