import {useQuery,useMutation,useQueryClient} from "@tanstack/react-query";
import {get,post} from "../lib/api";
import {useMe} from "../lib/auth";
export function DeviceImportance({mac,network,canWrite}:{mac:string;network?:string|null;canWrite:boolean}) {
 const me=useMe();const cache=useQueryClient();const key=["device-importance",me.data?.active_tenant?.id,network,mac];
 const path=`/api/v1/devices/${encodeURIComponent(mac)}/importance?network=${encodeURIComponent(network??"")}`;
 const query=useQuery({queryKey:key,queryFn:()=>get<{importance:string}>(path)});
 const save=useMutation({mutationFn:(importance:string)=>post(path,{importance}),onSuccess:()=>{void cache.invalidateQueries({queryKey:key});void cache.invalidateQueries({queryKey:["inboxTriage"]});}});
 return <label>Business importance <select value={query.data?.importance??"normal"} disabled={!canWrite||!query.data||save.isPending} onChange={e=>save.mutate(e.target.value)}><option value="normal">Normal</option><option value="important">Important</option><option value="critical">Business-critical</option></select>
 {save.isError&&<span role="alert">Could not save importance.</span>}</label>;
}
