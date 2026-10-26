"""Optional speech provider adapters. Nothing is sent externally unless configured and consented."""
from __future__ import annotations
import base64, os
import httpx

def provider_name() -> str:
    name=os.getenv("SPEECH_PROVIDER","browser").lower()
    return name if name in ("browser","sarvam","openai") else "browser"

def configured() -> bool:
    name=provider_name()
    return (name=="sarvam" and bool(os.getenv("SARVAM_API_KEY"))) or (name=="openai" and bool(os.getenv("OPENAI_API_KEY")))

async def transcribe(data:bytes,filename:str,content_type:str,language:str="unknown") -> dict:
    if len(data)>10*1024*1024: raise ValueError("Audio clips must be under 10 MB.")
    if not configured(): raise RuntimeError("External speech is not configured. Use browser speech input.")
    provider=provider_name()
    if provider=="sarvam":
        lang={"English":"en-IN","Hinglish":"unknown","Hindi":"hi-IN","Punjabi":"pa-IN","Tamil":"ta-IN"}.get(language,"unknown")
        async with httpx.AsyncClient(timeout=25) as client:
            response=await client.post("https://api.sarvam.ai/speech-to-text",headers={"api-subscription-key":os.environ["SARVAM_API_KEY"]},data={"model":"saaras:v4","language_code":lang},files={"file":(filename,data,content_type)})
            response.raise_for_status();payload=response.json()
        return {"transcript":payload.get("transcript",""),"language_code":payload.get("language_code"),"provider":"sarvam","request_id":payload.get("request_id")}
    from openai import OpenAI
    model=os.getenv("OPENAI_STT_MODEL","gpt-transcribe")
    client=OpenAI(api_key=os.environ["OPENAI_API_KEY"],timeout=25,max_retries=0)
    result=client.audio.transcriptions.create(model=model,file=(filename,data,content_type))
    return {"transcript":result.text,"language_code":None,"provider":"openai","request_id":None}

async def synthesize(text:str,language:str="Hindi") -> tuple[bytes,str]:
    if len(text)>2400: text=text[:2400]
    if not configured() or provider_name()!="sarvam": raise RuntimeError("Sarvam text-to-speech is not configured.")
    lang={"English":"en-IN","Hinglish":"hi-IN","Hindi":"hi-IN","Punjabi":"pa-IN","Tamil":"ta-IN"}.get(language,"hi-IN")
    payload={"text":text,"target_language_code":lang,"speaker":"shubh_enhi_ads","model":"bulbul:v4-flash","output_audio_codec":"mp3"}
    async with httpx.AsyncClient(timeout=25) as client:
        response=await client.post("https://api.sarvam.ai/text-to-speech",headers={"api-subscription-key":os.environ["SARVAM_API_KEY"],"Content-Type":"application/json"},json=payload)
        response.raise_for_status();result=response.json()
    audios=result.get("audios") or []
    if not audios: raise RuntimeError("Speech provider returned no audio.")
    return base64.b64decode(audios[0]),result.get("request_id","")
