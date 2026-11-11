"""Final-release audit/contract UI. Unknown restraint is inspectable, not runnable."""
import multiprocessing
import queue
import tkinter as tk
from tkinter import ttk
from .model import CamProject,TabReleaseOperation
from .plates import Plate
from .object_identity import object_identity_label
from .full_release import FullReleaseReview,RESTRAINT,install_release_stage,scope_fingerprint


def _worker(out,payload,source_id,plate_data,part_order,contract,commit,saved_order):
    try:
        project=CamProject.from_dict(payload)
        review=FullReleaseReview(project,source_id,Plate.from_dict(plate_data))
        if saved_order and saved_order.get('fingerprint')==review.fingerprint:
            order=saved_order['order']
        else:order=review.order_for_parts(part_order) if part_order else review.order
        plan=review.plan(order,contract,require_qualified=commit)
        if commit:
            result,stage=install_release_stage(project,plan)
            out.put((None,plan,{'project':result.to_dict(),'stage_id':stage.id},None))
        else:out.put((review,plan,None,None))
    except Exception as exc:out.put((None,None,None,str(exc)))


class FullReleaseDialog(tk.Toplevel):
    def __init__(self,parent,source_id,plate):
        super().__init__(parent)
        self.app=parent;self.project=parent.project
        self.document_id=parent.document_id;self.revision=parent.document_revision
        source=parent.project.metadata.get('source_plate')
        if source and plate.bounds==(0,0,source['width'],source['height']):plate=Plate.from_dict(source)
        self.plate=plate;self.source_id=source_id;self.result=None
        self.review=None;self.plan=None;self.part_order=[];self.worker=None;self.out=None;self.timer=None
        existing=next((o for o in self.project.operations if isinstance(o,TabReleaseOperation) and o.source_operation_id==source_id),None)
        self.saved=existing.release_plan if existing else {}
        self.title(f'Final Tab Release — source plate {plate.number}')
        self.geometry('1000x760');self.minsize(760,620);self.transient(parent)
        self.columnconfigure(0,weight=1);self.rowconfigure(1,weight=1)
        self.protocol('WM_DELETE_WINDOW',self.destroy)
        ttk.Label(self,text='X = 0: remove ALL original tabs in a separate final operation, after the reviewed machining prerequisites.\n'
                  'Apply replaces source cleanup, not its tab definitions. Drawing groups do not establish physical restraint.',
                  wraplength=930).grid(row=0,column=0,sticky='ew',padx=10,pady=8)
        body=ttk.Panedwindow(self,orient='horizontal');body.grid(row=1,column=0,sticky='nsew',padx=10)
        self.canvas=tk.Canvas(body,background='white');body.add(self.canvas,weight=3)
        right=ttk.Frame(body);body.add(right,weight=2)
        right.columnconfigure(0,weight=1);right.rowconfigure(0,weight=1)
        self.parts=ttk.Treeview(right,columns=('body','release','features'),show='headings',selectmode='browse')
        for key,title,width in (('body','Physical body / group',180),('release','Release event',85),('features','Features',60)):
            self.parts.heading(key,text=title);self.parts.column(key,width=width)
        self.parts.grid(row=0,column=0,sticky='nsew')
        sy=ttk.Scrollbar(right,orient='vertical',command=self.parts.yview);sy.grid(row=0,column=1,sticky='ns')
        sx=ttk.Scrollbar(right,orient='horizontal',command=self.parts.xview);sx.grid(row=1,column=0,sticky='ew')
        self.parts.configure(yscrollcommand=sy.set,xscrollcommand=sx.set)
        edit=ttk.Frame(right);edit.grid(row=2,column=0,sticky='ew',pady=4)
        ttk.Button(edit,text='Earlier',command=lambda:self.move(-1)).pack(side='left')
        ttk.Button(edit,text='Later',command=lambda:self.move(1)).pack(side='left')
        ttk.Button(edit,text='Re-audit order',command=self.preview).pack(side='left')
        ttk.Label(right,text='Loose-waste warnings — select to zoom').grid(row=3,column=0,sticky='w')
        self.warnings=ttk.Treeview(right,columns=('warning','size'),show='headings',height=4,selectmode='browse')
        self.warnings.heading('warning',text='Warning / cleanup');self.warnings.column('warning',width=190)
        self.warnings.heading('size',text=f'Size ({self.project.units})');self.warnings.column('size',width=130)
        self.warnings.grid(row=4,column=0,sticky='nsew');right.rowconfigure(4,weight=1)
        ws=ttk.Scrollbar(right,orient='vertical',command=self.warnings.yview);ws.grid(row=4,column=1,sticky='ns')
        self.warnings.configure(yscrollcommand=ws.set)
        ttk.Button(right,text='Show whole plate',command=self.show_plate).grid(row=5,column=0,sticky='w')
        self.warnings.bind('<<TreeviewSelect>>',lambda event:self.draw())
        self.parts.bind('<<TreeviewSelect>>',lambda event:self.draw())
        self.canvas.bind('<Configure>',lambda event:self.draw())
        self.summary=tk.StringVar(value='Building ownership, prerequisites and release chronology…')
        ttk.Label(self,textvariable=self.summary,wraplength=940,foreground='#205078').grid(row=2,column=0,sticky='ew',padx=10,pady=5)
        contract=ttk.LabelFrame(self,text='Required physical-restraint contract — not inferred or qualified by this software')
        contract.grid(row=3,column=0,sticky='ew',padx=10,pady=5)
        contract.columnconfigure(0,weight=1)
        self.confirm=tk.BooleanVar(value=False)
        ttk.Checkbutton(contract,text='Every listed physical body is independently secured after its last tab is cut; each stays within its original XY footprint\nand at/below the stock top through all cutting and subsequent travel. I have reviewed fixture clearances.',
                        variable=self.confirm,command=self.update_apply).grid(row=0,column=0,sticky='w',padx=6,pady=5)
        ttk.Label(contract,text='Reviewed fixture/restraint method or evidence (unknown/unverified vacuum is not sufficient):').grid(row=1,column=0,sticky='w',padx=6)
        self.note=tk.StringVar(value='')
        ttk.Entry(contract,textvariable=self.note).grid(row=2,column=0,sticky='ew',padx=6,pady=5)
        self.accept_waste=tk.BooleanVar(value=False)
        self.waste_check=ttk.Checkbutton(contract,text='I reviewed the highlighted loose-waste warnings and accept this release plan.',
            variable=self.accept_waste,command=self.update_apply)
        self.waste_check.grid(row=3,column=0,sticky='w',padx=6,pady=3)
        self.waste_check.grid_remove()
        self.trace=self.note.trace_add('write',lambda *_:self.update_apply())
        self.issue=tk.StringVar(value='Audit is available without confirmation. Apply remains disabled until all required inputs and geometry checks pass.')
        ttk.Label(self,textvariable=self.issue,wraplength=940,foreground='#9b4800').grid(row=4,column=0,sticky='ew',padx=10,pady=5)
        footer=ttk.Frame(self);footer.grid(row=5,column=0,sticky='ew',padx=10,pady=8)
        ttk.Button(footer,text='Cancel',command=self.destroy).pack(side='right')
        self.apply_button=ttk.Button(footer,text='Apply separate final release',command=self.accept,state='disabled')
        self.apply_button.pack(side='right',padx=5)
        ttk.Label(footer,text='No fixture, feed, depth, cutter or Safe-Z changes.').pack(side='left')
        self.grab_set();self.preview()

    def current(self):
        return self.app.project is self.project and self.app.document_id==self.document_id and self.app.document_revision==self.revision

    def contract(self):
        return {'restraint':RESTRAINT if self.confirm.get() else 'unknown',
                'confirmed_parts':list(self.review.parts) if self.review and self.confirm.get() else [],
                'evidence_note':self.note.get().strip(),
                'waste_warning_review':self.review.warning_review_key(self.plan['order'],self.plan['audit'])
                    if self.review and self.plan and self.accept_waste.get() else ''}

    def stop(self):
        if self.timer:self.after_cancel(self.timer);self.timer=None
        if self.worker:
            if self.worker.is_alive():self.worker.terminate()
            self.worker.join(timeout=.2);self.worker=None
        if self.out:self.out.close();self.out=None

    def start(self,commit=False):
        if not self.current():self.issue.set('Document changed; close and reopen this review.');return
        self.stop();self.apply_button.configure(state='disabled')
        context=multiprocessing.get_context('spawn');self.out=context.Queue()
        self.worker=context.Process(target=_worker,args=(self.out,self.project.to_dict(),self.source_id,
            self.plate.to_dict(),self.part_order,self.contract(),commit,self.plan or self.saved),daemon=True)
        self.worker.start();self.summary.set('Checking ownership, all prerequisites and every release/traverse… Cancel is available.')
        self.timer=self.after(80,self.poll)

    def preview(self):self.start()

    def poll(self):
        self.timer=None
        if not self.current():self.stop();self.issue.set('Document changed; worker result discarded.');return
        try:review,plan,result,error=self.out.get_nowait()
        except queue.Empty:
            if self.worker and not self.worker.is_alive():
                self.stop();self.issue.set('Review worker stopped without a result. No changes made.');return
            self.timer=self.after(80,self.poll);return
        self.stop()
        if error:
            self.review=None;self.plan=None;self.confirm.set(False)
            self.accept_waste.set(False);self.warnings.delete(*self.warnings.get_children())
            self.parts.delete(*self.parts.get_children());self.canvas.delete('all')
            self.issue.set(error);return
        if result:
            if scope_fingerprint(self.project,self.plate.to_dict())!=plan['input_fingerprint']:
                self.issue.set('Machining inputs changed; re-audit before applying.');return
            self.result=result;self.destroy();return
        prior_warning_key=self.contract().get('waste_warning_review')
        self.review,self.plan=review,plan
        if prior_warning_key!=review.warning_review_key(plan['order'],plan['audit']):
            self.accept_waste.set(False)
        if not self.part_order:self.part_order=list(dict.fromkeys(review.by_id[k]['owner'] for k in plan['order']))
        if self.saved:
            if self.saved.get('fingerprint')==review.fingerprint and self.saved.get('ownership')==review.ownership:
                self.confirm.set(self.saved.get('contract',{}).get('restraint')==RESTRAINT)
                self.note.set(self.saved.get('contract',{}).get('evidence_note',''))
                self.accept_waste.set(self.saved.get('contract',{}).get('waste_warning_review')==
                    review.warning_review_key(plan['order'],plan['audit']))
            self.saved={}
        self.refresh();self.update_apply()

    def refresh(self):
        if not self.review:return
        self.parts.delete(*self.parts.get_children())
        self.warnings.delete(*self.warnings.get_children())
        for index,warning in enumerate(self.plan['audit']['warnings']):
            x0,y0,x1,y1=warning['bounds']
            self.warnings.insert('','end',iid=str(index),values=(f'W{index+1}: {warning["message"]}',f'{x1-x0:.4g} × {y1-y0:.4g}'))
        if self.plan['audit']['warnings']:self.waste_check.grid()
        else:self.waste_check.grid_remove()
        entities={e.id:e for e in self.review.local.entities}
        events={owner:i+1 for i,event in enumerate(self.plan['audit']['events']) for owner in event['part_ids']}
        for owner in self.part_order:
            record=self.review.ownership[owner]
            name=object_identity_label(self.review.local,entities[owner],include_number_with_name=True)
            group=record['assembly_id']
            if group!=owner:name+=f' / {group[:16]}'
            self.parts.insert('','end',iid=owner,values=(name,events.get(owner,'Unresolved'),len(record['feature_ids'])))
        names={o.id:o.name for o in self.review.local.operations}
        later=', '.join(names[k] for k in self.review.later_ids) or 'none'
        self.summary.set(f'{len(self.review.parts)} physical bodies; {len(self.review.required_ids)} prerequisite operations; '
            f'{len(self.plan["order"])} cleanup spans; {len(self.plan["audit"]["events"])} geometric release events. '
            f'Operations after source, now before final release: {later}. Shared cuts can release multiple bodies in one event. '
            'Not a globally optimal or physically qualified sequence.')
        self.draw()

    def update_apply(self):
        if not hasattr(self,'apply_button'):return
        if not self.review or self.worker:
            self.apply_button.configure(state='disabled');return
        contract=self.contract()
        problems=self.review.contract_issues(contract)+self.plan['audit']['hazards']+self.review.warning_issues(self.plan['order'],self.plan['audit'],contract)
        self.issue.set('\n'.join(problems) if problems else
            f'Ready to apply. {len(self.plan["audit"]["warnings"])} loose-waste warning(s) reviewed. Warnings stay visible; this is not physical safety certification.')
        self.apply_button.configure(state='disabled' if problems or not self.current() else 'normal')

    def move(self,delta):
        selection=self.parts.selection()
        if not self.review or not selection or self.worker:return
        if self.review.tabs.locked:self.issue.set('Existing locked/manual source order is preserved.');return
        index=self.part_order.index(selection[0]);target=index+delta
        if 0<=target<len(self.part_order):
            self.part_order[index],self.part_order[target]=self.part_order[target],self.part_order[index]
            self.review=None;self.plan=None;self.parts.delete(*self.parts.get_children());self.canvas.delete('all')
            self.confirm.set(False);self.accept_waste.set(False);self.preview()

    def show_plate(self):
        self.warnings.selection_remove(self.warnings.selection());self.draw()

    def draw(self):
        self.canvas.delete('all')
        if not self.review:return
        w,h=max(100,self.canvas.winfo_width()),max(100,self.canvas.winfo_height())
        bounds=(0,0,self.plate.width,self.plate.height)
        picked=self.warnings.selection()
        if picked:
            x0,y0,x1,y1=self.plan['audit']['warnings'][int(picked[0])]['bounds']
            pad=max(x1-x0,y1-y0,self.review.radius*4)*.5
            bounds=(x0-pad,y0-pad,x1+pad,y1+pad)
        x0,y0,x1,y1=bounds
        scale=min((w-24)/(x1-x0),(h-24)/(y1-y0))
        def xy(x,y):return 12+(x-x0)*scale,h-12-(y-y0)*scale
        selected=set(self.parts.selection())
        events={owner:i+1 for i,event in enumerate(self.plan['audit']['events']) for owner in event['part_ids']}
        for owner,paths in self.review.parts.items():
            for path in paths:
                coords=[xy(x/1000000,y/1000000) for x,y in path]
                self.canvas.create_line(*(v for pt in coords+[coords[0]] for v in pt),fill='#b65300' if owner in selected else '#34729b',width=2)
            x,y=xy(*self.review.by_id[next(k for k in self.plan['order'] if self.review.by_id[k]['owner']==owner)]['mid'])
            self.canvas.create_text(x,y,text=str(events.get(owner,'?')),fill='#a04000',font=('Segoe UI',12,'bold'))
        for index,warning in enumerate(self.plan['audit']['warnings']):
            for path in warning['paths']:
                points=[xy(x,y) for x,y in path]
                self.canvas.create_line(*(v for pt in points+[points[0]] for v in pt),fill='#e8590c',width=3,tags=('loose_waste',f'warning:{index}'))
            bx0,by0,bx1,by1=warning['bounds'];x,y=xy((bx0+bx1)/2,(by0+by1)/2)
            self.canvas.create_oval(x-6,y-6,x+6,y+6,outline='#e8590c',width=2,tags=('loose_waste',f'warning:{index}'))
            self.canvas.create_text(x+9,y,text=f'W{index+1}',anchor='w',fill='#c2410c',tags=('loose_waste',f'warning:{index}'))

    def accept(self):
        if not self.review or str(self.apply_button['state'])=='disabled':return
        self.start(commit=True)

    def destroy(self):
        self.stop()
        if hasattr(self,'trace'):self.note.trace_remove('write',self.trace);del self.trace
        super().destroy()
