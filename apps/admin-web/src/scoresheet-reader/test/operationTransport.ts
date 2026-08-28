import {vi} from 'vitest';
export function installTransport(respond:(index:number,id:string,options:any)=>any){
 const calls:any[]=[],leases:any[]=[];
 vi.stubGlobal('fetch',vi.fn(async(input:any,options:any={})=>{
  const pathname=new URL(String(input),'http://synthetic.invalid').pathname;
  const match=pathname.match(/^\/api\/v1\/scoresheets\/([^/]+)\/(lease|recognition\/retry)$/);
  if(!match)throw new Error('UNEXPECTED_NETWORK_ROUTE '+pathname);
  const id=match[1],body=JSON.parse(options.body??'{}');
  if(match[2]==='lease'){leases.push({id,body});return new Response(JSON.stringify({read_only:false,read_only_reason:'',lease_token:'synthetic-lease-'+id,holder:null}),{status:200,headers:{'Content-Type':'application/json'}})}
  const headers=new Headers(options.headers);calls.push({id,body,key:headers.get('Idempotency-Key')});
  return respond(calls.length,id,options);
 }));
 return {calls,leases};
}
export function rawRun(id:string){return {id:'synthetic-new-run-'+id,document_id:id,base_revision:6,source_version:1,cycle:2,trigger:'MANUAL_RETRY',model:'MOCK_ONLY',prompt_version:'synthetic',image_sha256:'synthetic',auto_apply_allowed:true,can_retry:false,status:'QUEUED',attempt_count:0,max_attempts:4,next_attempt_at:null,last_error_code:'',last_error:'',recognition_notes:'',provider_usage:{},provider_result:null,applied_draft_version:null,created_at:'2026-08-28T00:00:00Z',updated_at:'2026-08-28T00:00:00Z'}}
export function response(value:any,status=200){return new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}})}
