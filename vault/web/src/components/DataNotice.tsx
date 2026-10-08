import {ago} from "../lib/format";
export function DataNotice({query}:{query:{isError:boolean;dataUpdatedAt:number;refetch:()=>unknown}}) {
 if (!query.isError) return null;
 return <div role="status" className="g-data-notice">Refresh failed. Showing the last successful data from {query.dataUpdatedAt?ago(new Date(query.dataUpdatedAt).toISOString()):"an unknown time"}. <button type="button" onClick={()=>void query.refetch()}>Retry</button></div>;
}
