"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Props<T>={items:T[];estimate?:number;height?:number;overscan?:number;render:(item:T,index:number)=>React.ReactNode;getKey?:(item:T,index:number)=>string|number};
export function VariableVirtualList<T>({items,estimate=180,height=720,overscan=5,render,getKey}:Props<T>){
  const [scrollTop,setScrollTop]=useState(0); const [heights,setHeights]=useState<Record<number,number>>({});
  const tops=useMemo(()=>{let y=0;return items.map((_,i)=>{const t=y;y+=heights[i]||estimate;return t})},[items,heights,estimate]);
  const total=(tops.length?tops[tops.length-1]:0)+(items.length?heights[items.length-1]||estimate:0);
  const start=Math.max(0,tops.findIndex(t=>t+(heights[tops.indexOf(t)]||estimate)>=scrollTop)-overscan);
  let end=start; while(end<items.length && tops[end] < scrollTop+height){end++} end=Math.min(items.length,end+overscan);
  const measure=useCallback((i:number,el:HTMLDivElement|null)=>{if(!el)return;const ro=new ResizeObserver(([entry])=>{const h=Math.ceil(entry.contentRect.height);setHeights(x=>x[i]===h?x:{...x,[i]:h})});ro.observe(el);return()=>ro.disconnect()},[]);
  useEffect(()=>setHeights({}),[items.length]);
  return <div style={{height,maxHeight:height,overflowY:"auto"}} onScroll={e=>setScrollTop(e.currentTarget.scrollTop)}><div style={{height:total,position:"relative"}}>{items.slice(start,end).map((item,offset)=>{const i=start+offset;return <div key={getKey?.(item,i)??i} ref={el=>measure(i,el)} style={{position:"absolute",top:tops[i],left:0,right:0}}>{render(item,i)}</div>})}</div></div>;
}
