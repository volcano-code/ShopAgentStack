/** Same-build manifest closure verification. Does not infer load time from bytes. */
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { pathToFileURL } from 'node:url';
import { measure } from './bundle-budget.mjs';
export function inspectAssistant(dist) {
 const report=measure(dist), manifest=JSON.parse(readFileSync(resolve(dist,'.vite/manifest.json'),'utf8'));
 const assistant=report.deferred.find(entry=>entry.key==='src/AgentWorkspace.tsx');
 if(!assistant?.closure) throw new Error('Missing assistant closure');
 const markdown=manifest['src/MarkdownContent.tsx'];
 return { assistant:assistant.closure, formatterDeferred: Boolean(markdown?.isDynamicEntry) && !assistant.closure.files.includes(markdown.file), totalJsBytes: report.totalJsBytes, manifestSha256:report.manifestSha256 };
}
export function decideAssistant(current,baseline) {
 if(!Number.isSafeInteger(baseline.assistant.bytes)||baseline.assistant.bytes<=0) throw new Error('Invalid baseline');
 const reduction=1-current.assistant.bytes/baseline.assistant.bytes;
 const passed=current.formatterDeferred && current.assistant.bytes<=340000 && reduction>=0.15;
 return {passed,reduction};
}
export function main(args) {
 if(args.length!==3) throw new Error('Expected current dist, baseline dist and output');
 const current=inspectAssistant(resolve(args[0])),baseline=inspectAssistant(resolve(args[1]));
 const result={schema:'shop.assistant-bundle.v1',current,baseline,...decideAssistant(current,baseline),caveat:'Static assistant entry closure before reply formatting. A nonempty conversation also needs the deferred formatter. Not load-time or full-history network savings.'};
 mkdirSync(dirname(resolve(args[2])),{recursive:true}); writeFileSync(args[2],JSON.stringify(result,null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify({passed:result.passed,bytes:current.assistant.bytes,reduction:result.reduction}));
 return result.passed?0:1;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href) { try{process.exitCode=main(process.argv.slice(2));}catch{console.error('Assistant bundle evidence unavailable');process.exitCode=2;} }
