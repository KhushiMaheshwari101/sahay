"""Small, local TF-IDF vector retrieval over approved text; no external vector service."""
from __future__ import annotations
import math,re
from collections import Counter
from pathlib import Path
from .config import ROOT

def _tokens(text: str) -> list[str]:
    words=re.findall(r"[a-z0-9]+",text.lower())
    synonyms={"emi":"installment monthly payment","charges":"charge fee cost","fees":"fee charge cost","prepayment":"foreclosure early close","interest":"rate","eligibility":"eligible rule qualify","documents":"document kyc identity","mandate":"nach debit","exclusions":"excluded not covered","premium":"price payment","coverage":"covered benefit"}
    return words+[syn for word in words for syn in synonyms.get(word,"").split()]

def _passages(text: str):
    title="General"; body=[]; sections=[]
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("# "):
            if body: sections.append((title,"\n".join(body)))
            title=line.lstrip("# ").strip(); body=[]
        elif line.strip(): body.append(line.strip())
        elif body and body[-1]!="": body.append("")
    if body: sections.append((title,"\n".join(body)))
    chunks=[]
    for heading,content in sections:
        parts=[part.strip() for part in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z0-9])",content) if part.strip()]
        for index in range(0,len(parts),3): chunks.append((heading if len(parts)<=3 else f"{heading} · part {index//3+1}"," ".join(parts[index:index+3])))
    return chunks

def search_text(query: str,text: str,source: str="uploaded PDF",limit:int=3):
    sections=_passages(text); query_terms=Counter(_tokens(query))
    if not query_terms:return []
    vectors=[Counter(_tokens(title+" "+body)) for title,body in sections]
    df={term:sum(term in vector for vector in vectors) for term in query_terms}
    idf={term:math.log(1+(len(sections)+1)/(df[term]+1))+1 for term in query_terms}
    q={term:(1+math.log(count))*idf[term] for term,count in query_terms.items()}; qnorm=math.sqrt(sum(v*v for v in q.values())) or 1
    ranked=[]
    for (title,body),vector in zip(sections,vectors):
        weights={term:(1+math.log(count))*idf[term] for term,count in vector.items() if term in idf}
        norm=math.sqrt(sum(v*v for v in weights.values())) or 1
        score=sum(q[t]*v for t,v in weights.items())/(qnorm*norm)
        if score>0: ranked.append((score,title,body))
    ranked.sort(key=lambda item:item[0],reverse=True)
    return [{"source":source,"section":title,"score":round(score,3),"text":body,"retrieval":"local_tfidf_vector"} for score,title,body in ranked[:limit] if score>=0.1]

def search_faq(query: str,limit:int=3):
    results=[]
    for filename in ("loan_faq.md","sample_loan_terms.txt","sample_policy_terms.txt"):
        path=ROOT/"data"/filename
        results.extend(search_text(query,path.read_text(encoding="utf-8-sig"),"data/"+filename,limit))
    results.sort(key=lambda item:item["score"],reverse=True)
    return results[:limit]

def explain_reason_codes(codes: list[str]) -> str:
    from .rules import reason_text
    return reason_text(codes)
