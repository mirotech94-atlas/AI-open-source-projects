import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { get, post } from "../lib/api";
import { useMe, roleOf, atLeast } from "../lib/auth";

export function TimezoneSettings() {
  const me = useMe();
  const cache = useQueryClient();
  const [zone, setZone] = useState("UTC");
  const [network, setNetwork] = useState("");
  const purpose = useQuery({queryKey:["site-context",me.data?.active_tenant?.id],enabled:!!me.data,queryFn:()=>get<{context:string}>("/api/v1/site-context")});
  const savePurpose = useMutation({mutationFn:(context:string)=>post("/api/v1/site-context",{context}),onSuccess:()=>cache.invalidateQueries({queryKey:["site-context"]})});
  const key = ["timezone", me.data?.active_tenant?.id, network];
  const networks = useQuery({ queryKey: ["networks", me.data?.active_tenant?.id],
    queryFn: () => get<{networks:{id:string;name:string}[]}>("/api/v1/networks"), enabled: !!me.data });
  const query = useQuery({ queryKey: key,
    queryFn: () => get<{timezone:string;inherited:boolean}>(`/api/v1/timezone?network=${encodeURIComponent(network)}`), enabled: !!me.data });
  useEffect(() => { if (query.data) setZone(query.data.timezone); }, [query.data]);
  const save = useMutation({ mutationFn: (timezone:string) => post("/api/v1/timezone", {timezone, network}),
    onSuccess: () => cache.invalidateQueries({queryKey:["timezone"]}) });
  const disabled = !atLeast(roleOf(me.data), "admin") || save.isPending || !query.data;
  return <fieldset className="g-settings-form g-settings-tz"><legend>Site timezone</legend>
    <label>Site use <select value={purpose.data?.context??"general"} disabled={!atLeast(roleOf(me.data),"admin")||savePurpose.isPending||!purpose.data} onChange={e=>savePurpose.mutate(e.target.value)}><option value="general">General network</option><option value="home">Home</option><option value="business">Business</option></select></label>
    {savePurpose.isError&&<span role="alert">Could not save site use.</span>}
    <p>Reports use the organization timezone. Device familiarity uses its network's timezone when overridden.</p>
    <label>Scope <select value={network} onChange={e => {setNetwork(e.target.value);save.reset();}}>
      <option value="">Organization default</option>
      {networks.data?.networks?.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
    </select></label>
    <label>Timezone <input value={zone} onChange={e => setZone(e.target.value)} placeholder="America/New_York" disabled={disabled}/></label>
    {network && query.data?.inherited && <span>Inherited from organization</span>}
    <button type="button" disabled={disabled} onClick={() => save.mutate(zone)}>Save timezone</button>
    {network && <button type="button" disabled={disabled} onClick={() => save.mutate("")}>Use organization default</button>}
    {save.isSuccess && <span role="status">Timezone saved</span>}
    {(save.isError || query.isError) && <span role="alert">Could not load or save this timezone. Check its name and try again.</span>}
  </fieldset>;
}
