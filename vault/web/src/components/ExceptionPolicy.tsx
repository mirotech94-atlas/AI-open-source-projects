import {useQuery,useMutation,useQueryClient} from "@tanstack/react-query";
import {get,post} from "../lib/api";
import {useMe} from "../lib/auth";
export function ExceptionPolicy({canWrite}:{canWrite:boolean}) {
 const me=useMe(); const cache=useQueryClient();
 const key=["exception-policy",me.data?.active_tenant?.id];
 const query=useQuery({queryKey:key,enabled:!!me.data,queryFn:()=>get<{require_expiry:boolean}>("/api/v1/settings/exception-policy")});
 const save=useMutation({mutationFn:(require_expiry:boolean)=>post<{require_expiry:boolean}>("/api/v1/settings/exception-policy",{require_expiry}),onSuccess:value=>cache.setQueryData(key,value)});
 return <fieldset><legend>Security exceptions</legend><p>A reason is always required when accepting a failing check.</p><label><input type="checkbox" checked={query.data?.require_expiry??false} disabled={!canWrite||!query.data||save.isPending} onChange={e=>save.mutate(e.target.checked)}/> Require an expiry for new accepted risks</label>{save.isSuccess&&<p role="status">Exception policy saved.</p>}{(query.isError||save.isError)&&<p role="alert">Could not load or save the policy. Try again.</p>}</fieldset>;
}
