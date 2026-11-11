"""Isolated modal review UI for staged tab cleanup; workers never touch Tk/jobs."""
import multiprocessing
import queue
import tkinter as tk
from tkinter import ttk

from .model import CamProject
from .object_identity import object_identity_label
from .plates import Plate
from .tab_release import CleanupReview, fingerprint


def _review_worker(out, payload, operation_id, plate_data, count, saved):
    try:
        project=CamProject.from_dict(payload)
        op=next(o for o in project.operations if o.id == operation_id)
        review=CleanupReview(project,op,Plate.from_dict(plate_data))
        if saved and saved.get("retain_per_part") == count and saved.get("fingerprint") == review.fingerprint:
            remove=saved["remove"]
            review.validate(remove,count)
        else:
            remove=review.propose(count)
        out.put((review,remove,None))
    except Exception as exc:
        out.put((None,None,str(exc)))


class TabCleanupDialog(tk.Toplevel):
    def __init__(self,parent,operation,plate, *, project=None, retain_count=None):
        super().__init__(parent)
        source=parent.project.metadata.get("source_plate")
        if source and plate.bounds == (0,0,source["width"],source["height"]):
            plate=Plate.from_dict(source)
        self.app=parent
        self.source_project=parent.project
        self.project=project if project is not None else parent.project
        self.revision=parent.document_revision
        self.document_id=parent.document_id
        self.operation=operation
        self.plate=plate
        self.result=None
        self.review=None
        self.remove=[]
        self.worker=None
        self.out=None
        self.timer=None
        self.title(f"Staged Tab Cleanup — Plate {plate.number} — {operation.name}")
        self.geometry("980x700")
        self.minsize(660,440)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW",self.destroy)
        self.rowconfigure(2,weight=1)
        self.columnconfigure(0,weight=1)
        ttk.Label(self,text="Keep every original tab during profiling; remove selected tabs only after all profiles finish.",
                  wraplength=850).grid(row=0,column=0,sticky="ew",padx=10,pady=(10,4))
        bar=ttk.Frame(self);bar.grid(row=1,column=0,sticky="ew",padx=10,pady=5)
        ttk.Label(bar,text="Minimum tabs to RETAIN per part:").pack(side="left")
        saved=operation.tab_release_plan
        self.count=tk.StringVar(value=str(retain_count if retain_count is not None else saved.get("retain_per_part",2)))
        self.count_entry=ttk.Entry(bar,textvariable=self.count,width=7);self.count_entry.pack(side="left",padx=6)
        self.propose_button=ttk.Button(bar,text="Propose cleanup",command=self.propose);self.propose_button.pack(side="left")
        ttk.Label(bar,text="For 0, use Final Tab Release Review").pack(side="left",padx=10)
        body=ttk.Panedwindow(self,orient="horizontal");body.grid(row=2,column=0,sticky="nsew",padx=10)
        self.canvas=tk.Canvas(body,background="white",highlightthickness=0);body.add(self.canvas,weight=3)
        right=ttk.Frame(body);body.add(right,weight=2)
        right.rowconfigure(0,weight=1);right.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(right,columns=("part","tab","action"),show="headings",selectmode="browse")
        for key,title,width in (("part","Part / object",125),("tab","Tab",40),("action","Retain / cleanup order",160)):
            self.tree.heading(key,text=title);self.tree.column(key,width=width)
        self.tree.grid(row=0,column=0,sticky="nsew")
        scroll=ttk.Scrollbar(right,orient="vertical",command=self.tree.yview);scroll.grid(row=0,column=1,sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal=ttk.Scrollbar(right,orient="horizontal",command=self.tree.xview)
        horizontal.grid(row=1,column=0,columnspan=2,sticky="ew")
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>",lambda event:self.draw())
        edits=ttk.Frame(right);edits.grid(row=2,column=0,columnspan=2,sticky="ew",pady=4)
        ttk.Button(edits,text="Retain / remove group",command=self.toggle).pack(side="left")
        ttk.Button(edits,text="Earlier",command=lambda:self.move(-1)).pack(side="left")
        ttk.Button(edits,text="Later",command=lambda:self.move(1)).pack(side="left")
        self.summary=tk.StringVar(value="Enter the number to retain per part, then Propose. No job changes until Apply.")
        ttk.Label(self,textvariable=self.summary,wraplength=910,foreground="#225080").grid(row=3,column=0,sticky="ew",padx=10,pady=5)
        ttk.Label(self,text="Green = retained. Orange numbers = later cleanup order. All parts remain attached in this stage.\n"
                  "Geometry checks perimeter connectivity, not clamp/vacuum strength. Extra tabs may be retained where required; fewer original tabs are all kept.\n"
                  "One operation on one full rectangular plate. Exact reviewed motions bypass later route optimization.\n"
                  "Fixtures, loose internal waste, other operations and full-release sequencing require separate manual review.",
                  wraplength=910).grid(row=4,column=0,sticky="ew",padx=10,pady=5)
        bottom=ttk.Frame(self);bottom.grid(row=5,column=0,sticky="ew",padx=10,pady=10)
        ttk.Button(bottom,text="Cancel",command=self.destroy).pack(side="right")
        self.apply_button=ttk.Button(bottom,text="Apply staged cleanup",command=self.accept,state="disabled")
        self.apply_button.pack(side="right",padx=5)
        ttk.Button(bottom,text="Clear saved plan / keep all tabs",command=self.clear).pack(side="left")
        self.canvas.bind("<Configure>",lambda event:self.draw())
        self.canvas.bind("<Button-1>",self.pick)
        self.trace=self.count.trace_add("write",lambda *_:self.invalidate())
        self.grab_set()
        if saved or retain_count is not None:
            self.propose(saved=saved)

    def current(self):
        return (self.app.project is self.source_project and self.app.document_revision == self.revision
                and self.app.document_id == self.document_id
                and self.operation in self.project.operations)

    def stop_worker(self):
        if self.timer:
            self.after_cancel(self.timer);self.timer=None
        if self.worker:
            if self.worker.is_alive():self.worker.terminate()
            self.worker.join(timeout=.2);self.worker=None
        if self.out:
            self.out.close();self.out=None

    def invalidate(self):
        self.stop_worker()
        self.review=None;self.remove=[]
        self.tree.delete(*self.tree.get_children());self.canvas.delete("all")
        self.apply_button.configure(state="disabled")
        self.propose_button.configure(state="normal")
        self.summary.set("Count changed. Propose again to review the new retained/removed tabs.")

    def propose(self,saved=None):
        self.invalidate()
        try:
            count=CleanupReview.count_value(self.count.get())
            if not self.current():raise ValueError("Document changed. Close and reopen this review.")
            context=multiprocessing.get_context("spawn")
            self.out=context.Queue()
            self.worker=context.Process(target=_review_worker,args=(self.out,self.project.to_dict(),
                self.operation.id,self.plate.to_dict(),count,saved),daemon=True)
            self.worker.start()
            self.propose_button.configure(state="disabled")
            self.summary.set("Checking actual cutter sweeps and shared bridges… You can cancel without changing the job.")
            self.timer=self.after(80,self.poll)
        except (ValueError,RuntimeError) as exc:self.summary.set(str(exc))

    def poll(self):
        self.timer=None
        if not self.current():
            self.invalidate();self.summary.set("Document changed; discarded the proposal. Close and reopen.");return
        try:review,remove,error=self.out.get_nowait()
        except queue.Empty:
            if self.worker and not self.worker.is_alive():
                self.stop_worker();self.propose_button.configure(state="normal")
                self.summary.set("Planning worker stopped without a result. No changes made.");return
            self.timer=self.after(80,self.poll);return
        self.stop_worker();self.propose_button.configure(state="normal")
        if error:self.summary.set(error);return
        self.review,self.remove=review,remove
        self.refresh()

    def refresh(self):
        if not self.review:return
        totals,retained=self.review.validate(self.remove,self.count.get())
        selected=self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        entities={e.id:e for e in self.review.project.entities}
        order={key:i+1 for i,key in enumerate(self.remove)}
        indexes={o:0 for o in totals}
        for span in self.review.spans:
            owner=span["owner"];indexes[owner]+=1
            name=object_identity_label(self.review.project,entities[owner],include_number_with_name=True)
            self.tree.insert("","end",iid=span["id"],values=(f"{name} ({retained[owner]}/{totals[owner]} kept)",
                indexes[owner],f"Remove #{order[span['id']]}" if span["id"] in order else "RETAIN"))
        if selected and self.tree.exists(selected[0]):self.tree.selection_set(selected)
        requested=int(self.count.get())
        extra=sum(n>requested for n in retained.values());few=sum(n<requested for n in totals.values())
        self.summary.set(f"{len(totals)} parts • {sum(totals.values())} original tabs • {len(self.remove)} cleanup cuts • "
            f"{sum(retained.values())} retained. {extra} parts need extra tabs for shared/support constraints; "
            f"{few} parts have fewer than requested (all retained). "
            +( "Existing locked/manual order preserved." if self.review.locked else "Deterministic travel order; no parts fully released."))
        self.apply_button.configure(state="normal");self.draw()

    def toggle(self):
        selected=self.tree.selection()
        if not self.review or not selected:return
        group=self.review.group(selected[0]);candidate=set(self.remove)
        candidate=candidate-group if selected[0] in candidate else candidate|group
        removed=[k for k in self.remove if k in candidate]
        removed += [s["id"] for s in self.review.spans if s["id"] in candidate and s["id"] not in removed]
        if self.review.locked:removed.sort(key=lambda k:self.review.by_id[k]["index"])
        try:self.review.validate(removed,self.count.get())
        except ValueError as exc:self.summary.set(str(exc));return
        self.remove=removed;self.refresh()

    def move(self,delta):
        selected=self.tree.selection()
        if not self.review or not selected or selected[0] not in self.remove:return
        if self.review.locked:self.summary.set("Manual/locked contour order cannot be reordered here.");return
        index=self.remove.index(selected[0]);target=index+delta
        if 0 <= target < len(self.remove):
            self.remove[index],self.remove[target]=self.remove[target],self.remove[index];self.refresh()

    def draw(self):
        self.canvas.delete("all")
        if not self.review:return
        w,h=max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height())
        scale=min((w-24)/self.plate.width,(h-24)/self.plate.height)
        def xy(x,y):return 12+x*scale,h-12-y*scale
        self.canvas.create_rectangle(*xy(0,0),*xy(self.plate.width,self.plate.height),outline="#888")
        for paths in self.review.parts.values():
            for path in paths:
                points=[xy(x/1000000,y/1000000) for x,y in path]
                self.canvas.create_line(*(v for p in points+[points[0]] for v in p),fill="#999")
        self.positions={};order={key:i+1 for i,key in enumerate(self.remove)}
        selected=set(self.tree.selection())
        for span in self.review.spans:
            key=span["id"];x,y=xy(*span["mid"]);self.positions[key]=(x,y)
            color="#bb5100" if key in order else "#06733c"
            self.canvas.create_oval(x-4,y-4,x+4,y+4,fill=color,outline="black" if key in selected else color,width=2)
            if key in order:self.canvas.create_text(x+5,y-7,text=str(order[key]),fill=color,anchor="sw",font=("Segoe UI",8))

    def pick(self,event):
        if not self.review:return
        key=min(self.positions,key=lambda k:(self.positions[k][0]-event.x)**2+(self.positions[k][1]-event.y)**2)
        self.tree.selection_set(key);self.tree.see(key);self.draw()

    def accept(self):
        if not self.review:return
        try:
            if not self.current():raise ValueError("Document changed; close and reopen.")
            if fingerprint(self.project,self.operation,self.plate.to_dict()) != self.review.fingerprint:
                raise ValueError("Machining inputs changed; re-plan before applying.")
            self.result=self.review.plan(self.remove,self.count.get())
        except ValueError as exc:
            self.summary.set(str(exc));self.apply_button.configure(state="disabled");return
        self.destroy()

    def clear(self):
        if not self.current():self.summary.set("Document changed; close and reopen.");return
        self.result={"clear":True}
        self.destroy()

    def destroy(self):
        self.stop_worker()
        if hasattr(self,"trace"):
            self.count.trace_remove("write",self.trace);del self.trace
        super().destroy()
