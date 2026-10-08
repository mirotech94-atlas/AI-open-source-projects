import {render,screen,cleanup} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import {CameraCensusPanel} from "../src/components/CameraCensusPanel";
afterEach(cleanup);
it("separates unobserved cameras from confirmed offline and avoids a false exposure guarantee",()=>{
 render(<CameraCensusPanel census={{known:9,online:0,expected:10,unobserved:10,offline:0,exposed:false,exposure_known:false,exposed_ports:[],gaps:[]}}/>);
 expect(screen.getByText(/9 discovered of 10 expected cameras; 0 answering/)).toBeInTheDocument();
 expect(screen.getByText(/10 unobserved now · 0 confirmed offline/)).toBeInTheDocument();
 expect(screen.getByText("Internet exposure has not been recently verified")).toBeInTheDocument();
 expect(screen.queryByText("none reachable from the internet")).not.toBeInTheDocument();
});
it("retains an old exposure finding while requesting fresh evidence",()=>{
 render(<CameraCensusPanel census={{known:1,online:0,expected:1,unobserved:1,offline:0,exposed:true,exposure_known:false,exposed_ports:[554],gaps:[]}}/>);
 expect(screen.getByText(/Previously reachable on ports 554; a fresh exposure check is needed/)).toBeInTheDocument();
});
