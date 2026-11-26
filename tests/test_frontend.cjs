const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes=new Map();
function node(){return {textContent:'',value:'all',children:[],classList:{add(){},remove(){},toggle(){}},style:{},append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},setAttribute(){},addEventListener(){}}}
const context={document:{getElementById(id){if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node},console,Date,Object,String};
vm.createContext(context);
vm.runInContext(fs.readFileSync('app/static/app.js','utf8').split('async function poll')[0],context);
const report={repository:'example/go',branch:'main',commit_sha:'a'.repeat(40),scanned_at:new Date().toISOString(),rule_version:'1.2.0',score:84,assessment:'unassessed',provisional:true,urgent:false,assessment_complete:false,assessment_notes:['依赖与启动未完整评估；总分最高 84 分。'],coverage:{complete:true,warnings:[],skipped:{},scanned_files:1,scanned_bytes:10,entries:1},dimensions:[],findings:[],ecosystems:['Go']};
context.sample=report; vm.runInContext('render(sample)',context);
assert.match(nodes.get('incomplete').textContent,/未完整评估/);
assert.doesNotMatch(nodes.get('incomplete').textContent,/跳过原因|文件扫描不完整/);
assert.ok(nodes.get('coverage').children.some(n=>n.textContent==='文件扫描范围已完成'));
report.coverage.complete=false;report.coverage.skipped={'超限':1};report.assessment_complete=true;report.assessment_notes=[];
vm.runInContext('render(sample)',context);
assert.match(nodes.get('incomplete').textContent,/文件扫描不完整/);
assert.match(nodes.get('incomplete').textContent,/跳过原因/);
assert.doesNotMatch(nodes.get('incomplete').textContent,/未完整评估/);
console.log('Frontend coverage/assessment text scenarios passed.');
