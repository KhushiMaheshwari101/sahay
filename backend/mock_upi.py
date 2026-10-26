"""Deterministic synthetic monthly inflow and UPI-like transactions for demo profiles."""
from __future__ import annotations
import random
from datetime import date
from .config import PROFILES

def generate_monthly_inflows(profile_id: str) -> list[dict]:
    if profile_id not in PROFILES: raise ValueError("Unknown synthetic profile.")
    p=PROFILES[profile_id]; count=min(12,int(p["active_months"])); rng=random.Random("sahay:"+profile_id)
    deviations=[rng.gauss(0,1) for _ in range(count)]; mean=sum(deviations)/max(1,count); deviations=[x-mean for x in deviations]; variance=(sum(x*x for x in deviations)/max(1,count))**0.5 or 1
    cv=float(p["inflow_variability"]); raw=[max(float(p["monthly_inflow"])*0.2,float(p["monthly_inflow"])*(1+cv*x/variance)) for x in deviations]; adjust=float(p["monthly_inflow"])/(sum(raw)/len(raw)); values=[x*adjust for x in raw]
    today=date.today(); result=[]
    for offset,value in enumerate(values):
        month=(today.month-count+offset-1)%12+1; year=today.year-1 if today.month-count+offset<0 else today.year
        result.append({"period":f"{year}-{month:02d}","inflow":round(value),"active":True})
    return result

def generate_transactions(profile_id: str) -> list[dict]:
    """Create fake, non-identifying credits/debits to make profile examples inspectable."""
    inflows=generate_monthly_inflows(profile_id); rng=random.Random("tx:"+profile_id); entries=[]
    for month_index,month in enumerate(inflows):
        credits=max(2,round(rng.uniform(2,5))); pieces=[rng.uniform(0.5,1.5) for _ in range(credits)]; total=sum(pieces)
        for i,weight in enumerate(pieces): entries.append({"month":month["period"],"day":min(27,2+i*5),"direction":"credit","amount":round(month["inflow"]*weight/total),"description":"Synthetic business or salary inflow"})
        for i in range(rng.randint(5,10)):
            entries.append({"month":month["period"],"day":min(27,3+i*2),"direction":"debit","amount":round(rng.uniform(250,4200)),"description":"Synthetic household or business spend"})
    return entries
