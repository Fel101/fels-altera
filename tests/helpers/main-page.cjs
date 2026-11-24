const fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
// The page's own scripts, in the order templates/index.html loads them.
const source=['appearance','welcome','app'].map(name=>fs.readFileSync(`static/js/${name}.js`,'utf8')).join('\n;\n'),template=fs.readFileSync('templates/index.html','utf8');
module.exports=function mainPage(options={}){
 const nodes=new Map(),calls=[],events={},windowEvents={},timers=new Map(),notices=[],errors=[],downloads=[],storage=new Map(),session=new Map();let serial=0,submitted=0;
 class Node{
  constructor(tag='div'){this.tag=tag;this.children=[];this.parent=null;this.className='';this.dataset={};this.style={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.attributes={};this.events={};this.html=null;
   this.classList={contains:name=>this.className.split(' ').includes(name),add:(...names)=>{this.className=[...new Set([...this.className.split(' '),...names])].join(' ').trim();},remove:(...names)=>{this.className=this.className.split(' ').filter(x=>!names.includes(x)).join(' ');},toggle:(name,force)=>{const on=force??!this.classList.contains(name);on?this.classList.add(name):this.classList.remove(name);return on;}};
  }
  get options(){return this.children;}get isConnected(){return document.body.contains(this);}
  set innerHTML(text){this.children=[];this.html=text;}get innerHTML(){return this.html??this.textContent.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
  append(...children){for(const node of children){if(node.parent)node.remove();node.parent=this;this.children.push(node);}}appendChild(node){this.append(node);return node;}
  replaceChildren(...children){this.children=[];this.html=null;this.append(...children);if(this.tag==='select')this.value=(children.find(x=>x.selected)||children[0])?.value||'';}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;}
  setAttribute(name,value){this.attributes[name]=value;if(name==='class')this.className=value;if(name.startsWith('data-'))this.dataset[name.slice(5)]=value;}
  removeAttribute(name){delete this.attributes[name];if(name==='title')this.title='';}getAttribute(name){return this.attributes[name]??null;}
  addEventListener(name,fn){(this.events[name]||=[]).push(fn);}dispatchEvent(event){for(const fn of this.events[event.type]||[])fn(event);return true;}
  async fire(name,event={}){await Promise.all((this.events[name]||[]).map(fn=>fn({target:this,preventDefault(){},...event})));}
  focus(){}click(){if(this.tag==='a')downloads.push({href:this.href,filename:this.download});return this.fire('click');}requestSubmit(){submitted++;}
  contains(node){return node===this||this.children.some(x=>x.contains?.(node));}
  matches(selector){if(selector.includes(','))return selector.split(',').some(x=>this.matches(x.trim()));if(selector.startsWith('#'))return this.id===selector.slice(1);if(selector.startsWith('.'))return selector.slice(1).split('.').every(name=>this.classList.contains(name));return this.tag===selector;}
  querySelectorAll(selector){return this.children.flatMap(x=>x.matches?[(x.matches(selector)?x:null),...x.querySelectorAll(selector)].filter(Boolean):[]);}querySelector(selector){return this.querySelectorAll(selector)[0]||null;}closest(selector){for(let n=this;n;n=n.parent)if(n.matches(selector))return n;return null;}
 }
 const document={body:new Node('body'),documentElement:new Node('html'),hidden:false,visibilityState:'visible',createElement:tag=>new Node(tag),
  getElementById:id=>nodes.get(id)||document.body.querySelector('#'+id),querySelectorAll:selector=>document.body.querySelectorAll(selector),querySelector:selector=>document.body.querySelector(selector),addEventListener(name,fn){(events[name]||=[]).push(fn);}};
 for(const match of template.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)){const node=new Node(match[1]);node.id=match[2];nodes.set(node.id,node);document.body.append(node);}
 for(const mode of ['dark','light','midnight']){const node=new Node('button');node.className='theme-option '+mode+'-mode';document.body.append(node);}
 const store=map=>({getItem:key=>map.has(key)?map.get(key):null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)});
 const context=vm.createContext({document,crypto:webcrypto,AbortController,TextDecoder,TextEncoder,Blob,FormData,Event,Option:function(text,value,_,selected){Object.assign(this,{text,value,selected});},
  navigator:{onLine:true},location:{origin:'http://synthetic.invalid',reload(){throw Error('Unexpected navigation');}},console:{warn:(...args)=>errors.push(args),error:(...args)=>errors.push(args),log(){}},
  localStorage:store(storage),sessionStorage:store(session),__SESSION_ID__:1,__DATA_EPOCH__:'synthetic-one',AlteraAPI:{epoch:'synthetic-one'},
  setTimeout(fn,delay){const id=++serial;timers.set(id,{fn,delay,repeat:false});return id;},clearTimeout:id=>timers.delete(id),setInterval(fn,delay){const id=++serial;timers.set(id,{fn,delay,repeat:true});return id;},clearInterval:id=>timers.delete(id),cancelAnimationFrame(){},
  URL:class extends URL{static createObjectURL(){return'blob:synthetic-'+ ++serial;}static revokeObjectURL(){}},confirm:()=>options.confirm!==false,alert:message=>notices.push(message),
  addEventListener(name,fn){(windowEvents[name]||=[]).push(fn);},openCurrentCompanion:options.openCurrent||(async()=>true),
  async fetch(url,init={}){const call={url,...init,body:typeof init.body==='string'?JSON.parse(init.body):init.body};calls.push(call);if(options.intercept){const reply=await options.intercept(call);if(reply!==undefined)return reply;}
   let data={};if(url==='/api/llm/providers')data={providers:[{key:'lmstudio',name:'Synthetic local',is_active:true},{key:'custom',name:'Synthetic custom'}],active_provider:'lmstudio',active_model:'synthetic-model'};
   else if(url.includes('/models'))data={models:['synthetic-model']};else if(url==='/api/voice/status')data={tts_enabled:false,stt_available:false};else if(url==='/api/offline/status')data={online:true};
   else if(url.startsWith('/api/health/details'))data={llm:{status:'ok'},voice:{status:'disabled'},memory:{status:'ok'}};else if(url==='/api/tools/files')data={files:[]};else if(url==='/api/onboarding/status')data={complete:true};
   else if(url==='/api/profile')data={name:'Synthetic user'};else if(url==='/api/conversation-starters')data={starters:[]};else if(url==='/api/export')data={synthetic:true};else if(url.startsWith('/api/messages/'))data=[];
   return{ok:true,status:200,json:async()=>structuredClone(data),blob:async()=>new Blob([JSON.stringify(data)])};}
 });context.window=context;
 require('./ordinary-page.cjs')(context);
 vm.runInContext(source,context);context.showReplyNotice=message=>notices.push(message);
 return{context,nodes,calls,timers,notices,errors,downloads,session,
  run:code=>vm.runInContext(code,context),async ready(){return Promise.all((events.DOMContentLoaded||[]).map(fn=>fn()));},
  async tick(){await new Promise(resolve=>setImmediate(resolve));},get submitted(){return submitted;},
  unload(){let prevented=false;const event={preventDefault(){prevented=true;}};for(const fn of windowEvents.beforeunload||[])fn(event);return prevented;},
  async fireTimer(delay){const found=[...timers].find(([,item])=>item.delay===delay);if(!found)return false;const[id,item]=found;if(!item.repeat)timers.delete(id);await item.fn();return true;}};
};
