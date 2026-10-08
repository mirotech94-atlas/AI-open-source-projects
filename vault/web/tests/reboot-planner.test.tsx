import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {render, screen, cleanup, waitFor} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {afterEach, expect, it, vi} from "vitest";
import {RebootPlanner} from "../src/components/RebootPlanner";

const {status, show} = vi.hoisted(()=>({status:vi.fn(), show:vi.fn()}));
vi.mock("../src/lib/api",()=>({queueStatus:status}));
vi.mock("../src/design",()=>({useToast:()=>({show})}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
const plan={hosts:[{host:"web",reason:"Kernel update",services:["web server"],queued:false}],windows:[{label:"Tonight UTC",start:"2099-01-01T02:00:00Z",end:"2099-01-01T03:00:00Z"}]};
function mount(terminal:string) {
 status.mockResolvedValue({id:"job-1",status:terminal});
 const schedule=vi.fn().mockResolvedValue({id:"job-1",status:"scheduled"});
 const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
 render(<QueryClientProvider client={client}><RebootPlanner plan={plan} onSchedule={schedule}/></QueryClientProvider>);
 return schedule;
}
it.each(["failed","missed","cancelled"])("never labels a %s reboot recovered",async terminal=>{
 mount(terminal);
 await userEvent.click(screen.getByRole("button",{name:"Schedule reboot"}));
 expect(await screen.findByText(`Reboot ${terminal}. Review the job in Activity before scheduling again.`)).toBeInTheDocument();
 expect(screen.queryByText(/services confirmed back up/i)).not.toBeInTheDocument();
 expect(status).toHaveBeenCalledWith("job-1");
});
it("keeps command completion separate from recovery",async()=>{
 mount("done");
 await userEvent.click(screen.getByRole("button",{name:"Schedule reboot"}));
 expect(await screen.findByText("Command completed. Recovery verification is shown in Activity.")).toBeInTheDocument();
});
it("requires a future chosen time and submits a bounded timezone-aware window",async()=>{
 const schedule=mount("scheduled");
 await userEvent.selectOptions(screen.getByRole("combobox",{name:"Window"}),"custom");
 expect(screen.getByRole("button",{name:"Schedule reboot"})).toBeDisabled();
 await userEvent.type(screen.getByLabelText("Reboot start time"),"2099-01-01T02:00");
 await userEvent.click(screen.getByRole("button",{name:"Schedule reboot"}));
 await waitFor(()=>expect(schedule).toHaveBeenCalled());
 const window=schedule.mock.calls[0][1];
 expect(window.start).toMatch(/Z$/);
 expect(Date.parse(window.end)-Date.parse(window.start)).toBe(300_000);
});
