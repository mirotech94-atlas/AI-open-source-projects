import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { get, post } from "./api";
import { roleOf, useMe } from "./auth";

export interface PersonalPreferences {
  view: "owner" | "customer" | "technician";
  density: "compact" | "comfortable";
  show_ticker: boolean;
  theme: "system" | "light" | "dark";
  saved_views?: {name:string;url:string}[];
}

export function usePreferences() {
  const me = useMe();
  const cache = useQueryClient();
  const key = ["preferences", me.data?.user.username];
  const role = roleOf(me.data);
  const fallback: PersonalPreferences = {
    view: role === "viewer" ? "customer" : role === "operator" ? "technician" : "owner",
    density: role === "viewer" ? "comfortable" : "compact",
    show_ticker: role !== "viewer", theme: "system",
  };
  const query = useQuery({queryKey:key, enabled:!!me.data, queryFn:() => get<PersonalPreferences>("/api/v1/preferences")});
  const mutation = useMutation({
    mutationFn:(value:PersonalPreferences) => post<PersonalPreferences>("/api/v1/preferences",value),
    onSuccess:(value) => cache.setQueryData(key,value),
  });
  return { value:query.data ?? fallback, ready:!!query.data || query.isError, save:mutation.mutate,
    pending:mutation.isPending, error:mutation.error ?? query.error };
}
