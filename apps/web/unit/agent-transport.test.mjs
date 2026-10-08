import test from "node:test";
import assert from "node:assert/strict";
import {EventDecoder, applyEvent, createLatch, followAgentRun, requestAgent, runValue} from "../src/agentTransport.ts";
const enc = new TextEncoder();
const event = (id, text="你好") => ({id,kind:"assistant_delta",data:{message_id:"m",text}});
const frame = (e,nl="\n") => `id: ${e.id}${nl}data: ${JSON.stringify(e)}${nl}${nl}`;
const run = (status="RUNNING",events=[]) => ({id:"r",input:"合成请求",provider:"fixture",status,events});
const response = text => new Response(text,{headers:{"content-type":"text/event-stream"}});
for (const newline of ["\n","\r\n","\r"]) test(`incremental UTF8, CR/LF and comments ${JSON.stringify(newline)}`,()=>{
 const parser=new EventDecoder(), bytes=enc.encode(`: heartbeat${newline}${newline}`+frame(event(1),newline));
 const out=[]; for(const byte of bytes) out.push(...parser.push(Uint8Array.of(byte)));
 // A CR at a chunk boundary waits for the following byte to disambiguate CRLF.
 if(newline==="\r")out.push(...parser.push(enc.encode(": heartbeat\r\r")));
 assert.deepEqual(out,[event(1)]);
});
test("multiline data fields and optional space",()=>{
 const parser=new EventDecoder();assert.deepEqual(parser.push(enc.encode('data:{"id":1,\ndata: "kind":"assistant",\ndata:"data":{"text":"合成"}}\n\n')),[{id:1,kind:"assistant",data:{text:"合成"}}]);
});
test("replays are deduplicated using the last accepted ID",()=>{
 const parser=new EventDecoder(2);assert.deepEqual(parser.push(enc.encode(frame(event(2))+frame(event(3))+frame(event(3)))),[event(3)]);
});
test("unterminated frame is not interpreted as completed output",()=>assert.deepEqual(new EventDecoder().push(enc.encode(frame(event(1)).slice(0,-1))),[]));
test("bounded frames reject both long data and heartbeat accumulation",()=>{
 assert.throws(()=>new EventDecoder().push(enc.encode("x".repeat(262145))));
 assert.throws(()=>new EventDecoder().push(enc.encode(": "+"x".repeat(262144)+"\n")));
});
for(const value of [{id:0,kind:"state",data:{}},{id:1,kind:"state",data:[]},{id:"1",kind:"state",data:{}}]) test(`invalid event is refused ${JSON.stringify(value)}`,()=>assert.throws(()=>new EventDecoder().push(enc.encode(frame(value)))));
test("malformed event content never escapes in error text",()=>assert.throws(()=>new EventDecoder().push(enc.encode("data: SECRET-RAW-RESPONSE\n\n")),e=>!e.message.includes("SECRET")));
test("idempotent event merge",()=>{const r=run("RUNNING",[event(1)]);assert.equal(applyEvent(r,event(1)),r);assert.equal(applyEvent(r,{id:2,kind:"state",data:{status:"STOPPED"}}).status,"STOPPED");});
test("synchronous mutation latch rejects double click in same tick",()=>{const lock=createLatch();assert.equal(lock.enter(),true);assert.equal(lock.enter(),false);lock.leave();assert.equal(lock.enter(),true);});
test("HTML 401 invalidates login BEFORE JSON parsing",async()=>{
 let expired=0;await assert.rejects(requestAgent("/runs/r","synthetic",undefined,undefined,{fetcher:async()=>new Response("<html>PRIVATE</html>",{status:401}),onUnauthorized:()=>expired++}),e=>e.status===401&&!e.message.includes("PRIVATE"));assert.equal(expired,1);
});
test("failed mutations are never retried by HTTP helper",async()=>{
 let calls=0;await assert.rejects(requestAgent("/runs/r/confirm","synthetic",{},undefined,{fetcher:async()=>{calls++;throw new TypeError("NetworkError");}}));assert.equal(calls,1);
});
test("mismatched run snapshots fail closed",()=>assert.throws(()=>runValue(run(),"other")));
const options = (extra={})=>({signal:new AbortController().signal,onEvent(){},onSnapshot(){},onConnection(){},sleep:async()=>{},...extra});
test("early EOF reads persisted state then reconnects using cursor; no mutation",async()=>{
 const calls=[],events=[],states=[];let streams=0;
 await followAgentRun("r","synthetic",options({onEvent:e=>events.push(e),onConnection:s=>states.push(s),fetcher:async(url,init)=>{
  calls.push([url,init.method||"GET"]);
  if(url.includes("/events?")){streams++;return response(streams===1?frame(event(1)):frame(event(1))+frame(event(2)));}
  return Response.json(run(streams===1?"RUNNING":"COMPLETED",streams===1?[event(1)]:[event(1),event(2)]));
 }}));
 assert.deepEqual(events,[event(1),event(2)]);assert.equal(calls[2][0],"/api/agent/runs/r/events?after=1");assert.ok(calls.every(x=>x[1]==="GET"));assert.equal(states.at(-1),"idle");
});
test("persistent disconnect exhausts bounded budget and exposes paused state",async()=>{
 let streams=0;const states=[];await followAgentRun("r","synthetic",options({onConnection:s=>states.push(s),fetcher:async url=>{if(url.includes("/events?")){streams++;throw new TypeError("offline");}return Response.json(run());}}));
 assert.equal(streams,3);assert.equal(states.at(-1),"paused");
});
test("stream non-JSON 401 stops immediately without retries or snapshot",async()=>{
 let calls=0,expired=0;await followAgentRun("r","synthetic",options({onUnauthorized:()=>expired++,fetcher:async()=>{calls++;return new Response("not-json",{status:401});}}));assert.equal(calls,1);assert.equal(expired,1);
});
test("unmount abort cancels reader without late events or snapshots",async()=>{
 const abort=new AbortController();let late=0,cancelled=false;
 const stream=new ReadableStream({start(){},cancel(){cancelled=true;}});
 const promise=followAgentRun("r","synthetic",options({signal:abort.signal,onEvent:()=>late++,onSnapshot:()=>late++,fetcher:async()=>new Response(stream,{headers:{"content-type":"text/event-stream"}})}));
 await new Promise(resolve=>setTimeout(resolve,5));abort.abort();await promise;assert.equal(late,0);assert.equal(cancelled,true);
});
test("idle stream is bounded and recovers terminal server state",async()=>{
 let cancelled=false,final;await followAgentRun("r","synthetic",options({idleMs:10,onSnapshot:r=>final=r,fetcher:async url=>url.includes("/events?")?new Response(new ReadableStream({cancel(){cancelled=true;}}),{headers:{"content-type":"text/event-stream"}}):Response.json(run("STOPPED"))}));assert.equal(cancelled,true);assert.equal(final.status,"STOPPED");
});

test("unknown persisted status cannot masquerade as a terminal state",()=>assert.throws(()=>runValue(run("NEW_UNKNOWN"),"r")));
test("unknown state event is refused",()=>assert.throws(()=>new EventDecoder().push(enc.encode(frame({id:1,kind:"state",data:{status:"DONE_TRUST_ME"}})))));
