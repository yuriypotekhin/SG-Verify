// Scene graph panel: episode goal, roles -> object names, the graph of the current frame and edge editing.
// Data are the same ObjectStates as in the objects sidebar:
// nodes = tracks/masks (one role = one node), edges = tags `relation` / `action`.

import './styles.scss';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import Layout from 'antd/lib/layout';
import AutoComplete from 'antd/lib/auto-complete';
import Select from 'antd/lib/select';
import Radio from 'antd/lib/radio';
import Button from 'antd/lib/button';
import Input from 'antd/lib/input';
import InputNumber from 'antd/lib/input-number';
import Modal from 'antd/lib/modal';
import Popover from 'antd/lib/popover';
import Text from 'antd/lib/typography/Text';
import { MenuFoldOutlined, MenuUnfoldOutlined, CheckOutlined, CloseCircleOutlined } from '@ant-design/icons';

import { CombinedState, ActiveControl } from 'reducers';
import {
    ObjectType, getCore, ObjectState, Label, JobStage, JobState,
} from 'cvat-core-wrapper';
import {
    updateAnnotationsAsync, createAnnotationsAsync, removeObjectAsync, fetchAnnotationsAsync, saveAnnotationsAsync, changeFrameAsync,
    updateActiveControl,
} from 'actions/annotation-actions';
import { updateJobAsync } from 'actions/jobs-actions';

const core = getCore();

type Scope = 'frame' | 'run' | 'toEnd' | 'all' | 'range';
type Kind = 'relation' | 'action';

interface Edge {
    kind: Kind;
    from: string;   // role
    to: string;     // role or 'none'
    value: string;  // relation / action value
    state: ObjectState;
}

interface Node {
    role: string;
    name: string;
    color: string;
}

interface Meta {
    episode?: string;
    goal?: string;
    entities?: { entity_id: string; role: string; name: string; level?: number }[];   // level: row of the node in the graph layout (0 = top)
    ontology?: { states?: string[]; actions?: string[] };
    merges?: Record<string, string>;   // role -> role it was merged into (the same object)
}

const KIND_FIELDS: Record<Kind, [string, string, string]> = {
    relation: ['subject', 'relation', 'object'],
    action: ['actor', 'action', 'object'],
};

function attrMap(label: Label): Record<string, number> {
    const out: Record<string, number> = {};
    label.attributes.forEach((a) => { out[a.name] = a.id as number; });
    return out;
}

function readEdge(state: ObjectState): Edge | null {
    const kind = state.label.name as Kind;
    if (!(kind in KIND_FIELDS)) return null;
    const ids = attrMap(state.label);
    const [f, v, t] = KIND_FIELDS[kind];
    return {
        kind,
        from: state.attributes[ids[f]],
        value: state.attributes[ids[v]],
        to: state.attributes[ids[t]] || 'none',
        state,
    };
}

function sameEdge(a: Edge, b: Edge): boolean {
    return a.kind === b.kind && a.from === b.from && a.to === b.to && a.value === b.value;
}

// the task guide carries sg-meta: a goal and/or a list of objects (a scene description instead of a goal)
const hasMeta = (m: Meta): boolean => !!(m.goal || (m.entities && m.entities.length));

function parseMeta(markdown: string): Meta {
    const m = markdown.match(/<!--\s*sg-meta\s+(\{[\s\S]*?\})\s*-->/);
    if (!m) return {};
    try { return JSON.parse(m[1]); } catch { return {}; }
}

// Fixed layout by rows: sg-meta entities[].level; without levels the nodes fill rows as wide as the panel allows.
// It does not depend on edges, so the graph does not jump when relations or loops are added.
const NODE_SLOT = 170;   // horizontal room per node when the rows are filled automatically
function layout(nodes: Node[], width: number, rowH: number, levels: Record<string, number> = {}) {
    const layers: string[][] = [];
    const hasLevels = Object.keys(levels).length > 0;
    const perRow = Math.max(1, Math.floor(width / NODE_SLOT));
    nodes.forEach((n, i) => {
        // nodes without a level go to the middle row when other nodes have levels
        const l = hasLevels ? (n.role in levels ? levels[n.role] : 1) : Math.floor(i / perRow);
        (layers[l] = layers[l] || []).push(n.role);
    });
    const used = layers.filter(Boolean);
    const pos: Record<string, { x: number; y: number }> = {};
    used.forEach((layer, li) => {
        layer.forEach((role, i) => {
            pos[role] = { x: ((i + 0.5) * width) / layer.length, y: 40 + li * rowH };
        });
    });
    return { pos, rows: used.length };
}

export default function SceneGraphSideBar(): JSX.Element {
    const dispatch = useDispatch();
    const states = useSelector((s: CombinedState) => s.annotation.annotations.states) as ObjectState[];
    const frame = useSelector((s: CombinedState) => s.annotation.player.frame.number);
    const jobInstance = useSelector((s: CombinedState) => s.annotation.job.instance);
    const labels = useSelector((s: CombinedState) => s.annotation.job.labels) as Label[];
    const activatedStateID = useSelector((s: CombinedState) => s.annotation.annotations.activatedStateID);
    const me = useSelector((s: CombinedState) => s.auth.user);
    const shapeOpacity = useSelector((s: CombinedState) => s.settings.shapes.opacity);
    const selectedOpacity = useSelector((s: CombinedState) => s.settings.shapes.selectedOpacity);

    const [collapsed, setCollapsed] = useState(false);
    const [meta, setMeta] = useState<Meta>({});
    const [selected, setSelected] = useState<Edge | null>(null);
    const [adding, setAdding] = useState<Kind | null>(null);      // "add edge" mode
    const [pendingFrom, setPendingFrom] = useState<string | null>(null);
    const [newFromTo, setNewFromTo] = useState<[string, string] | null>(null);
    const [newValue, setNewValue] = useState<string>('');
    const [editValue, setEditValue] = useState<string>('');
    const [scope, setScope] = useState<Scope>('run');
    const [range, setRange] = useState<[number, number]>([0, 0]);
    const [busy, setBusy] = useState(false);
    const [rename, setRename] = useState<{ role: string; name: string } | null>(null);
    const [goalEdit, setGoalEdit] = useState<string | null>(null);   // editing the episode goal (sg-meta goal)
    const [panelW, setPanelW] = useState<number>(() => {
        try { return Number(localStorage.getItem('sg-panel-width')) || 376; } catch { return 376; }
    });

    // resize the panel by dragging its right edge
    const startResize = (ev: React.MouseEvent): void => {
        ev.preventDefault();
        const x0 = ev.clientX; const w0 = panelW;
        const move = (m: MouseEvent): void => setPanelW(Math.min(1000, Math.max(260, w0 + m.clientX - x0)));
        const up = (): void => {
            window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up);
            window.dispatchEvent(new Event('resize'));   // the CVAT canvas recomputes its size
        };
        window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
    };
    useEffect(() => { try { localStorage.setItem('sg-panel-width', String(panelW)); } catch { /* ignore */ } }, [panelW]);

    const [task, setTask] = useState<any>(null);
    const [verdict, setVerdict] = useState<string>('');   // '' | 'verified' | 'goal_mismatch'
    useEffect(() => {
        if (!jobInstance) return;
        // the goal and roles live in the TASK guide; jobInstance.guide() of a task in a project would return the project guide
        jobInstance.guide().then((g: any) => { if (g && hasMeta(parseMeta(g.markdown))) setMeta(parseMeta(g.markdown)); }).catch(() => {});
        core.tasks.get({ id: jobInstance.taskId }).then(async ([t]: any[]) => {
            setTask(t); setVerdict(t?.subset || '');
            try {
                const tg = t ? await t.guide() : null;
                if (tg && hasMeta(parseMeta(tg.markdown))) setMeta(parseMeta(tg.markdown));
            } catch { /* ignore */ }
            // an episode without a verdict opened by me is "in progress" for me: yellow badge in the task list
            if (t && !t.subset && me && (!t.assignee || t.assignee.id !== me.id)) {
                try { t.assignee = me; await t.save(); } catch { /* ignore */ }
            }
            if (jobInstance.state === JobState.NEW) {
                try { await dispatch(updateJobAsync(jobInstance, { state: JobState.IN_PROGRESS })); } catch { /* ignore */ }
            }
        }).catch(() => {});
    }, [jobInstance]);

    // which model is behind the tracking buttons: the SAM3 service names itself in /sg-tools/health, SAM2 does not;
    // mask refinement by clicks (/refine) exists only in the SAM3 service; no service at all -> the SAM buttons are hidden
    const [samName, setSamName] = useState('SAM2');
    const [samRefine, setSamRefine] = useState(false);
    const [samAvailable, setSamAvailable] = useState(false);
    useEffect(() => {
        fetch('/sg-tools/health').then((r) => {
            if (!r.ok) throw new Error(String(r.status));
            return r.json();
        }).then((h: any) => {
            if (h?.model) setSamName(String(h.model).toUpperCase());
            setSamRefine(!!h?.refine);
            setSamAvailable(true);
        }).catch(() => setSamAvailable(false));
    }, []);

    // next episode of the project without a verdict: by name after the current one (as in the task list), then from the start;
    // episodes "in progress" for another reviewer are skipped. Its job is opened directly, without going back to the list.
    const openNextEpisode = async (): Promise<boolean> => {
        if (!task) return false;
        const unchecked = { '!': { in: [{ var: 'subset' }, ['verified', 'goal_mismatch']] } };
        const query = async (cond: any[]): Promise<any[]> => {
            const p = new URLSearchParams({ filter: JSON.stringify({ and: [unchecked, ...cond] }), sort: 'name', page_size: '50' });
            if (task.projectId) p.set('project_id', String(task.projectId));
            const r = await fetch(`/api/tasks?${p}`);
            if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
            return (await r.json()).results || [];
        };
        const free = (t: any): boolean => t.id !== task.id && (!t.assignee || (!!me && t.assignee.id === me.id));
        const next = (await query([{ '>': [{ var: 'name' }, task.name] }])).find(free) || (await query([])).find(free);
        if (!next) return false;
        const jr = await fetch(`/api/jobs?task_id=${next.id}`);
        if (!jr.ok) throw new Error(`${jr.status} ${await jr.text()}`);
        const job = ((await jr.json()).results || [])[0];
        if (!job) return false;
        window.location.assign(`/tasks/${next.id}/jobs/${job.id}`);
        return true;
    };

    // the verdict is stored in task.subset (the task list filters by it); after a verdict the next episode opens
    const setEpisodeVerdict = async (value: 'verified' | 'goal_mismatch'): Promise<void> => {
        setBusy(true);
        try {
            await dispatch(saveAnnotationsAsync());
            if (jobInstance.annotations.hasUnsavedChanges()) {
                Modal.error({ title: 'Not saved', content: 'Annotations were not saved, so the verdict is not set. Press Save and try again.' });
                return;
            }
            await dispatch(updateJobAsync(jobInstance, {
                stage: JobStage.ACCEPTANCE,
                state: value === 'verified' ? JobState.COMPLETED : JobState.REJECTED,
            }));
            if (task) { task.subset = value; if (me) task.assignee = me; await task.save(); setVerdict(value); }   // assignee = who gave the verdict
            try {
                if (await openNextEpisode()) return;
                Modal.success({ title: 'All episodes checked', content: 'Saved. No episodes without a verdict are left in this project.' });
            } catch (e: any) {
                Modal.warning({ title: 'Saved, but the next episode did not open', content: String(e?.message || e) });
            }
        } finally { setBusy(false); }
    };

    const relationLabel = labels.find((l) => l.name === 'relation');
    const actionLabel = labels.find((l) => l.name === 'action');
    const suggestions = (kind: Kind): string[] => (kind === 'relation' ? meta.ontology?.states : meta.ontology?.actions) || [];

    const { nodes, edges } = useMemo(() => {
        const byRole: Record<string, Node> = {};
        const es: Edge[] = [];
        states.forEach((st) => {
            if (st.objectType === ObjectType.TAG) {
                const e = readEdge(st);
                if (e) es.push(e);
            } else if (st.objectType === ObjectType.SHAPE || st.objectType === ObjectType.TRACK) {
                const role = st.label.name;
                const name = st.attributes[attrMap(st.label).name] || '';
                if (!byRole[role] || (!byRole[role].name && name)) {
                    byRole[role] = { role, name, color: st.label.color || '#888' };
                }
            }
        });
        // roles from the guide without a box or a mask on this frame are shown pale
        (meta.entities || []).forEach((e) => {
            if (!byRole[e.role]) {
                const lab = labels.find((l) => l.name === e.role);
                byRole[e.role] = { role: e.role, name: e.name, color: lab?.color || '#888' };
            }
        });
        // roles merged into another one (the same object) are not shown as separate nodes
        Object.keys(meta.merges || {}).forEach((r) => { delete byRole[r]; });
        // node order is constant over the whole video: as in the episode's object list (guide), otherwise by role name
        // (obj2 < obj10); otherwise it would depend on the order of shapes on the frame and the graph would jump between frames
        const rank: Record<string, number> = {};
        (meta.entities || []).forEach((e, i) => { if (!(e.role in rank)) rank[e.role] = i; });
        const ordered = Object.values(byRole).sort((a, b) => ((rank[a.role] ?? 1e6) - (rank[b.role] ?? 1e6))
            || a.role.localeCompare(b.role, undefined, { numeric: true }));
        return { nodes: ordered, edges: es };
    }, [states, meta, labels]);

    // role caption including merges, e.g. `support & target`
    const roleTitle = (role: string): string => {
        const extra = Object.entries(meta.merges || {}).filter(([, to]) => to === role).map(([from]) => from);
        return [role, ...extra].join(' & ');
    };

    const W = panelW - 16;
    const ROW = 120;
    const levels = useMemo(() => Object.fromEntries((meta.entities || []).filter((e) => typeof e.level === 'number')
        .map((e) => [e.role, e.level as number])), [meta]);
    const { pos, rows } = useMemo(() => layout(nodes, W, ROW, levels), [nodes, W, levels]);
    const H = Math.max(140, 40 + ROW * (rows - 1) + 90);
    const visibleRoles = new Set(states
        .filter((s) => s.objectType !== ObjectType.TAG && !(s.objectType === ObjectType.TRACK && s.outside))
        .map((s) => s.label.name));

    // ---- frames an edit applies to
    const framesFor = (sc: Scope): number[] => {
        if (!jobInstance) return [frame];
        let start = sc === 'all' ? jobInstance.startFrame : frame;
        let stop = sc === 'frame' ? frame : jobInstance.stopFrame;
        if (sc === 'range') {
            start = Math.max(jobInstance.startFrame, Math.min(range[0], range[1]));
            stop = Math.min(jobInstance.stopFrame, Math.max(range[0], range[1]));
        }
        const out: number[] = [];
        for (let f = start; f <= stop; f++) out.push(f);
        return out;
    };

    // "this run": from the current frame in both directions while the neighbouring frames have the same edge
    const runFrames = async (edge: Edge): Promise<number[]> => {
        const has = async (f: number): Promise<boolean> => (await edgesOnFrame(f)).some((e) => sameEdge(e, edge));
        const out = [frame];
        for (let f = frame - 1; f >= jobInstance.startFrame && await has(f); f--) out.unshift(f);
        for (let f = frame + 1; f <= jobInstance.stopFrame && await has(f); f++) out.push(f);
        return out;
    };

    const statesOnFrame = async (f: number): Promise<ObjectState[]> => (
        f === frame ? states : jobInstance.annotations.get(f, false, [])
    );
    const edgesOnFrame = async (f: number): Promise<Edge[]> => (
        (await statesOnFrame(f)).filter((s) => s.objectType === ObjectType.TAG).map(readEdge).filter(Boolean) as Edge[]
    );

    const refresh = async (sc: Scope): Promise<void> => {
        if (sc !== 'frame') await dispatch(fetchAnnotationsAsync());
    };

    // changing a value = a new tag with the new value + deleting the old one: CVAT marks the new object
    // as source=manual, so the export shows that a human edited it
    const changeEdge = async (edge: Edge, value: string, sc: Scope): Promise<void> => {
        if (!value || value === edge.value) { setSelected(null); return; }
        setBusy(true);
        try {
            const created: ObjectState[] = [];
            const old: [number, ObjectState][] = [];
            for (const f of sc === 'run' ? await runFrames(edge) : framesFor(sc)) {
                (await edgesOnFrame(f)).filter((e) => sameEdge(e, edge)).forEach((e) => {
                    const ids = attrMap(e.state.label);
                    const [ff, v, t] = KIND_FIELDS[e.kind];
                    created.push(new core.classes.ObjectState({
                        objectType: ObjectType.TAG, label: e.state.label, frame: f,
                        attributes: { [ids[ff]]: e.from, [ids[v]]: value, [ids[t]]: e.to },
                    }));
                    old.push([f, e.state]);
                });
            }
            for (const [f, st] of old) {
                if (f === frame) await dispatch(removeObjectAsync(st, true));
                else await st.delete(f, true);
            }
            if (created.length) await dispatch(createAnnotationsAsync(created));
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); setSelected(null); }
    };

    const deleteEdge = async (edge: Edge, sc: Scope): Promise<void> => {
        setBusy(true);
        try {
            for (const f of sc === 'run' ? await runFrames(edge) : framesFor(sc)) {
                for (const e of (await edgesOnFrame(f)).filter((x) => sameEdge(x, edge))) {
                    if (f === frame) await dispatch(removeObjectAsync(e.state, true));
                    else await e.state.delete(f, true);
                }
            }
            await refresh(sc);
        } finally { setBusy(false); setSelected(null); }
    };

    const addEdge = async (kind: Kind, from: string, to: string, value: string, sc0: Scope): Promise<void> => {
        const sc: Scope = sc0 === 'run' ? 'all' : sc0;
        const label = kind === 'relation' ? relationLabel : actionLabel;
        if (!label || !value) return;
        setBusy(true);
        try {
            const ids = attrMap(label);
            const [f, v, t] = KIND_FIELDS[kind];
            const probe: Edge = { kind, from, to, value, state: null as any };
            const created: ObjectState[] = [];
            for (const fr of framesFor(sc)) {
                if ((await edgesOnFrame(fr)).some((e) => sameEdge(e, probe))) continue;
                created.push(new core.classes.ObjectState({
                    objectType: ObjectType.TAG, label, frame: fr,
                    attributes: { [ids[f]]: from, [ids[v]]: value, [ids[t]]: to },
                }));
            }
            if (created.length) { await dispatch(createAnnotationsAsync(created)); await refresh(sc); }
        } finally { setBusy(false); setAdding(null); setPendingFrom(null); setNewFromTo(null); setNewValue(''); }
    };

    // object name: on all frames, for all boxes and masks of this role
    const renameRole = async (role: string, name: string): Promise<void> => {
        setBusy(true);
        try {
            const toSave: ObjectState[] = [];
            for (const f of framesFor('all')) {
                (await statesOnFrame(f)).forEach((s) => {
                    if (s.objectType !== ObjectType.TAG && s.label.name === role) {
                        const id = attrMap(s.label).name;
                        if (s.attributes[id] !== name) { s.attributes = { [id]: name }; toSave.push(s); }
                    }
                });
            }
            await Promise.all(toSave.map((s) => s.save()));
            if ((meta.entities || []).some((e) => e.role === role && e.name !== name)) {
                await saveMeta({ ...meta, entities: (meta.entities || []).map((e) => (e.role === role ? { ...e, name } : e)) });
            }
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); setRename(null); }
    };



    // ---- timeline: which edges are on which frames (over all frames of the episode)
    interface Row { key: string; kind: Kind; from: string; to: string; value: string; frames: Set<number>; }
    const [tlRows, setTlRows] = useState<Row[]>([]);
    useEffect(() => {
        let alive = true;
        (async () => {
            if (!jobInstance) return;
            const acc: Record<string, Row> = {};
            for (let f = jobInstance.startFrame; f <= jobInstance.stopFrame; f++) {
                const sts: ObjectState[] = f === frame ? states : await jobInstance.annotations.get(f, false, []);
                sts.forEach((st) => {
                    if (st.objectType !== ObjectType.TAG) return;
                    const e = readEdge(st); if (!e) return;
                    const key = `${e.kind}|${e.from}|${e.to}|${e.value}`;
                    (acc[key] = acc[key] || { key, kind: e.kind, from: e.from, to: e.to, value: e.value, frames: new Set() }).frames.add(f);
                });
            }
            const rank: Record<string, number> = {};
            (meta.entities || []).forEach((e, i) => { if (!(e.role in rank)) rank[e.role] = i; });
            const lvl = (r: string): number => (r in rank ? rank[r] : 1e6);
            const list = Object.values(acc).sort((x, y) => (x.kind === y.kind ? 0 : x.kind === 'relation' ? -1 : 1)
                || lvl(x.from) - lvl(y.from) || x.from.localeCompare(y.from, undefined, { numeric: true })
                || x.value.localeCompare(y.value));
            if (alive) setTlRows(list);
        })();
        return () => { alive = false; };
    }, [states, jobInstance, meta]);

    // ---- tools for the boxes of the active track
    const [boxRange, setBoxRange] = useState<[number, number]>([0, 0]);
    useEffect(() => { setBoxRange([frame, jobInstance?.stopFrame ?? frame]); }, [activatedStateID]);
    // A click on an object on the frame pins its ROLE; hovering over other objects
    // on the way to the panel does not change it. The buttons work with both the box and the mask of this role.
    const [pinnedRole, setPinnedRole] = useState<string | null>(null);
    const statesRef = useRef<ObjectState[]>(states);
    const activatedRef = useRef<number | null>(activatedStateID);
    useEffect(() => { statesRef.current = states; }, [states]);
    useEffect(() => { activatedRef.current = activatedStateID; }, [activatedStateID]);
    useEffect(() => {
        const onDown = (e: MouseEvent): void => {
            const el = e.target as HTMLElement | null;
            if (!el || !el.closest('.cvat-canvas-container')) return;
            if (refineRef.current) return;   // clicks of mask refinement are points, not a selection of another object
            window.setTimeout(() => {
                const id = activatedRef.current;
                if (id === null) return;
                const st = statesRef.current.find((x) => x.clientID === id);
                if (st && st.objectType !== ObjectType.TAG) setPinnedRole(st.label.name);
            }, 60);
        };
        window.document.addEventListener('mousedown', onDown, true);
        return () => window.document.removeEventListener('mousedown', onDown, true);
    }, []);

    const liveRole = states.find((st) => st.clientID === activatedStateID && st.objectType !== ObjectType.TAG)?.label.name || null;
    const role = pinnedRole || liveRole;
    const activeTrack = role ? states.find((st) => st.objectType === ObjectType.TRACK && st.label.name === role) || null : null;
    const activeMask = role ? states.find((st) => st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === role) || null : null;
    const activeObj = activeTrack || activeMask;
    const boxFrames = (): number[] => {
        const a = Math.max(jobInstance.startFrame, Math.min(boxRange[0], boxRange[1]));
        const b = Math.min(jobInstance.stopFrame, Math.max(boxRange[0], boxRange[1]));
        const out: number[] = [];
        for (let f = a; f <= b; f++) out.push(f);
        return out;
    };
    // remove keyframes strictly between X and Y: CVAT interpolates the box linearly
    const interpolateBox = async (): Promise<void> => {
        if (!activeTrack) return;
        setBusy(true);
        try {
            const fr = boxFrames();
            for (const f of fr.slice(1, -1)) {
                const st = (await statesOnFrame(f)).find((x) => x.clientID === activeTrack.clientID);
                if (st && st.keyframe) { st.keyframe = false; await st.save(); }
            }
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); }
    };
    // copy the box of the current frame to the range as is (static objects)
    const copyBox = async (): Promise<void> => {
        if (!activeTrack || !activeTrack.points) return;
        setBusy(true);
        try {
            const pts = [...activeTrack.points];
            for (const f of boxFrames()) {
                if (f === frame) continue;
                const st = (await statesOnFrame(f)).find((x) => x.clientID === activeTrack.clientID);
                if (st) { st.points = [...pts]; st.keyframe = true; st.outside = false; await st.save(); }
            }
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); }
    };

    // the object left the frame: mark the box as outside on the range (a second call reverts it)
    const setOutside = async (value: boolean): Promise<void> => {
        if (!activeTrack) return;
        setBusy(true);
        try {
            for (const f of boxFrames()) {
                const st = (await statesOnFrame(f)).find((x) => x.clientID === activeTrack.clientID);
                if (st && st.outside !== value) { st.outside = value; st.keyframe = true; await st.save(); }
            }
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); }
    };
    const deleteMasks = async (): Promise<void> => {
        if (!activeObj) return;
        const role = activeObj.label.name;
        setBusy(true);
        try {
            let n = 0;
            for (const f of boxFrames()) {
                for (const st of await statesOnFrame(f)) {
                    if (st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === role) {
                        if (f === frame) await dispatch(removeObjectAsync(st, true)); else await st.delete(f, true);
                        n += 1;
                    }
                }
            }
            await dispatch(fetchAnnotationsAsync());
            setTracking(`${n} masks deleted`);
        } finally { setBusy(false); }
    };

    // the mask of the current frame -> the whole range (static objects); old masks of this role in the range are replaced
    const copyMask = async (): Promise<void> => {
        if (!activeMask || !activeMask.points) return;
        setBusy(true);
        try {
            const pts = [...activeMask.points];
            const created: ObjectState[] = [];
            for (const f of boxFrames()) {
                if (f === frame) continue;
                const sts = await statesOnFrame(f);
                for (const st of sts) {
                    if (st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === activeMask.label.name) {
                        await st.delete(f, true);
                    }
                }
                created.push(new core.classes.ObjectState({
                    objectType: ObjectType.SHAPE, shapeType: 'mask' as any, label: activeMask.label, frame: f,
                    points: [...pts], attributes: { ...activeMask.attributes }, group: activeMask.group as any,
                }));
            }
            if (created.length) await dispatch(createAnnotationsAsync(created));
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); }
    };

    // SAM2/SAM3: the mask of the current frame -> masks (and boxes from them) on frames up to the end of the range
    const [tracking, setTracking] = useState<string>('');
    // boxes of the object (track of the role) on frames with new masks: keyframe = box of the mask. annotations.get with allTracks
    // returns the track also on frames before its start (as outside), so a keyframe there extends the track backwards. If there is
    // no track at all (an object without masks in the package), it is created on the first of the frames.
    const setTrackBoxes = async (
        roleName: string, label: Label, attributes: Record<number, string>, boxes: Record<number, number[]>,
    ): Promise<number> => {
        const frames = Object.keys(boxes).map(Number).sort((a, b) => a - b);
        if (!frames.length || !jobInstance) return 0;
        const trackOn = async (f: number): Promise<ObjectState | undefined> => (
            (await jobInstance.annotations.get(f, true, []) as ObjectState[])
                .find((x) => x.objectType === ObjectType.TRACK && x.label.name === roleName)
        );
        let rest = frames;
        if (!(await trackOn(frames[0]))) {
            await dispatch(createAnnotationsAsync([new core.classes.ObjectState({
                objectType: ObjectType.TRACK, shapeType: 'rectangle' as any, label, frame: frames[0],
                points: boxes[frames[0]], attributes: { ...attributes }, occluded: false, zOrder: 0,
            })]));
            rest = frames.slice(1);
        }
        for (const f of rest) {
            const st = await trackOn(f);
            if (!st) continue;
            st.points = boxes[f]; st.keyframe = true; st.outside = false;
            await st.save();
        }
        return frames.length;
    };

    const trackMask = async (): Promise<void> => {
        if (!activeMask || !activeMask.points || !jobInstance) return;
        const episode = meta.episode || task?.name;
        if (!episode) { Modal.error({ title: 'No episode name', content: 'Guide has no sg-meta; cannot locate frames.' }); return; }
        const pts = activeMask.points;
        const [l, t, r, b] = pts.slice(-4);
        const to = boxRange[1] === frame ? boxRange[0] : boxRange[1];
        if (to === frame) return;
        setBusy(true); setTracking(`${samName}: tracking to frame ${to}…`);
        try {
            const resp = await fetch('/sg-tools/sam2/track', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    episode, frame, to_frame: to,
                    mask: { rle: pts.slice(0, -4), left: l, top: t, width: r - l + 1, height: b - t + 1 },
                }),
            });
            if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
            const data = await resp.json();
            const created: ObjectState[] = [];
            const boxes: Record<number, number[]> = {};
            for (const fr of data.frames as any[]) {
                if (!fr.rle) continue;
                const sts = await statesOnFrame(fr.frame);
                for (const st of sts) {
                    if (st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === activeMask.label.name) {
                        await st.delete(fr.frame, true);
                    }
                }
                boxes[fr.frame] = [fr.bbox[0], fr.bbox[1], fr.bbox[2], fr.bbox[3]];
                created.push(new core.classes.ObjectState({
                    objectType: ObjectType.SHAPE, shapeType: 'mask' as any, label: activeMask.label, frame: fr.frame,
                    points: [...fr.rle, fr.left, fr.top, fr.left + fr.width - 1, fr.top + fr.height - 1],
                    attributes: { ...activeMask.attributes }, group: activeMask.group as any,
                }));
            }
            if (created.length) await dispatch(createAnnotationsAsync(created));
            // a box on every frame with a new mask; no track or frames before its start: the track is created / extended
            const updatedBoxes = await setTrackBoxes(activeMask.label.name, activeMask.label, { ...activeMask.attributes }, boxes);
            await dispatch(fetchAnnotationsAsync());
            setTracking(`${samName}: ${created.length} masks, ${updatedBoxes} boxes updated`);
        } catch (e: any) {
            Modal.error({ title: `${samName} tracking failed`, content: String(e?.message || e) });
            setTracking('');
        } finally { setBusy(false); }
    };

    // clear the verdict: the task is "not checked" again, the job is in progress
    const clearVerdict = async (): Promise<void> => {
        setBusy(true);
        try {
            await dispatch(updateJobAsync(jobInstance, { stage: JobStage.ANNOTATION, state: JobState.IN_PROGRESS }));
            if (task) { task.subset = ''; await task.save(); setVerdict(''); }
        } finally { setBusy(false); }
    };

    // masks from boxes: where the object has a box but no mask, SAM2/SAM3 builds the mask from the box
    const masksFromBoxes = async (onlyMissing = true): Promise<void> => {
        if (!activeObj || !jobInstance) return;
        const episode = meta.episode || task?.name;
        if (!episode) { Modal.error({ title: 'No episode name', content: 'Guide has no sg-meta; cannot locate frames.' }); return; }
        const role = activeObj.label.name;
        setBusy(true); setTracking(`${samName}: building masks from boxes…`);
        try {
            const need: { frame: number; box: number[] }[] = [];
            const perFrame: Record<number, ObjectState[]> = {};
            for (const f of boxFrames()) {
                const sts = await statesOnFrame(f);
                perFrame[f] = sts;
                const box = sts.find((st) => st.objectType === ObjectType.TRACK && st.label.name === role && !st.outside);
                const mask = sts.find((st) => st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === role);
                if (!box || !box.points) continue;
                if (onlyMissing && mask) continue;
                need.push({ frame: f, box: box.points.slice(0, 4) });
            }
            if (!need.length) { setTracking(`${samName}: every frame in the range already has a mask`); return; }
            const resp = await fetch('/sg-tools/sam2/from_box', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ episode, boxes: need }),
            });
            if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
            const data = await resp.json();
            const created: ObjectState[] = [];
            const label = labels.find((l) => l.name === role) as Label;
            const nameAttr = attrMap(label).name;
            const nameValue = activeObj.attributes[attrMap(activeObj.label).name] || '';
            for (const fr of data.frames as any[]) {
                if (!fr.rle) continue;
                for (const st of perFrame[fr.frame] || []) {
                    if (st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === role) {
                        await st.delete(fr.frame, true);
                    }
                }
                created.push(new core.classes.ObjectState({
                    objectType: ObjectType.SHAPE, shapeType: 'mask' as any, label, frame: fr.frame,
                    points: [...fr.rle, fr.left, fr.top, fr.left + fr.width - 1, fr.top + fr.height - 1],
                    attributes: { [nameAttr]: nameValue },
                }));
            }
            if (created.length) await dispatch(createAnnotationsAsync(created));
            await dispatch(fetchAnnotationsAsync());
            setTracking(`${samName}: ${created.length} masks built from boxes`);
        } catch (e: any) {
            Modal.error({ title: `${samName} failed`, content: String(e?.message || e) });
            setTracking('');
        } finally { setBusy(false); }
    };

    // ---- marking segments on the timeline: a click on the row name -> marking mode,
    // left click on a cell = start of a segment, right click = end, a second click removes the mark,
    // hovering moves the frame, N (or the check mark) applies.
    interface Paint { kind: Kind; from: string; to: string; value: string; starts: number[]; ends: number[]; }
    const [paint, setPaint] = useState<Paint | null>(null);
    const [newEdge, setNewEdge] = useState<{ kind: Kind; from: string; to: string; value: string } | null>(null);

    const paintFrames = (p: Paint): Set<number> => {
        const out = new Set<number>();
        if (!jobInstance) return out;
        const starts = [...p.starts].sort((a, b) => a - b);
        const ends = [...p.ends].sort((a, b) => a - b);
        if (!starts.length && ends.length) {
            for (let f = jobInstance.startFrame; f <= ends[0]; f++) out.add(f);
            return out;
        }
        let ei = 0;
        for (const st of starts) {
            while (ei < ends.length && ends[ei] < st) ei += 1;
            const en = ei < ends.length ? ends[ei] : jobInstance.stopFrame;
            for (let f = st; f <= en; f++) out.add(f);
            if (ei < ends.length) ei += 1;
        }
        return out;
    };

    const toggleMark = (p: Paint, f: number, isStart: boolean): Paint => {
        const key = isStart ? 'starts' : 'ends';
        const other = isStart ? 'ends' : 'starts';
        const has = (p as any)[key].includes(f);
        return {
            ...p,
            [key]: has ? (p as any)[key].filter((x: number) => x !== f) : [...(p as any)[key], f],
            [other]: (p as any)[other].filter((x: number) => x !== f),
        } as Paint;
    };

    // CVAT catches N before us and enters drawing mode: switch it off after applying
    const canvasInstance = useSelector((s: CombinedState) => s.annotation.canvas.instance);
    const cancelDrawing = (): void => {
        try { (canvasInstance as any)?.draw({ enabled: false }); } catch { /* ignore */ }
        try { (canvasInstance as any)?.cancel(); } catch { /* ignore */ }
        dispatch(updateActiveControl(ActiveControl.CURSOR));
    };

    const applyPaint = async (): Promise<void> => {
        if (!paint || !jobInstance) return;
        cancelDrawing();
        const target = paintFrames(paint);
        const label = paint.kind === 'relation' ? relationLabel : actionLabel;
        if (!label) { setPaint(null); return; }
        setBusy(true);
        try {
            const ids = attrMap(label);
            const [ff, vv, tt] = KIND_FIELDS[paint.kind];
            const probe = { kind: paint.kind, from: paint.from, to: paint.to, value: paint.value } as Edge;
            const created: ObjectState[] = [];
            for (let f = jobInstance.startFrame; f <= jobInstance.stopFrame; f++) {
                const found = (await edgesOnFrame(f)).find((e) => sameEdge(e, probe));
                if (target.has(f) && !found) {
                    created.push(new core.classes.ObjectState({
                        objectType: ObjectType.TAG, label, frame: f,
                        attributes: { [ids[ff]]: paint.from, [ids[vv]]: paint.value, [ids[tt]]: paint.to },
                    }));
                } else if (!target.has(f) && found) {
                    if (f === frame) await dispatch(removeObjectAsync(found.state, true));
                    else await found.state.delete(f, true);
                }
            }
            if (created.length) await dispatch(createAnnotationsAsync(created));
            await dispatch(fetchAnnotationsAsync());
        } finally { setBusy(false); setPaint(null); window.setTimeout(cancelDrawing, 50); }
    };

    useEffect(() => {
        if (!paint) return undefined;
        const onKey = (e: KeyboardEvent): void => {
            const tag = (e.target as HTMLElement)?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') return;
            // intercept before CVAT hotkeys (for CVAT, N finishes a shape)
            if (e.key === 'n' || e.key === 'N' || e.key === 'Enter') {
                e.preventDefault(); e.stopPropagation(); applyPaint();
            }
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancelDrawing(); setPaint(null); }
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [paint, frame, states]);

    // ---- mask refinement by clicks (SAM3) on the current frame. Uses the CVAT canvas points mode: left click adds a part,
    // right click removes a part, a click on a point deletes it; a preview of the new mask after every click. Enter / N applies,
    // Esc cancels. "edit parts": the part under each click is added to / subtracted from the mask, the rest stays;
    // "from scratch": the object is segmented from the points only (when the mask is completely wrong).
    interface Refine {
        role: string; frame: number; episode: string; label: Label;
        attributes: Record<number, string>; group: any; maskID: number | null;
        mask: { rle: number[]; left: number; top: number; width: number; height: number } | null;
        scratch: boolean; points: number[][]; labels: number[]; result: any | null;
    }
    const [refining, setRefining] = useState<Refine | null>(null);
    const refineRef = useRef<Refine | null>(null);
    const refineSeq = useRef(0);
    const putRefine = (r: Refine | null): void => { refineRef.current = r; setRefining(r); };

    const runRefine = async (r: Refine): Promise<void> => {
        const seq = ++refineSeq.current;
        const showPreview = (res: any): void => {
            const shapes = res?.rle ? [{
                shapeType: 'mask', points: [...res.rle, res.left, res.top, res.left + res.width - 1, res.top + res.height - 1],
            }] : [];
            try { (canvasInstance as any)?.interact({ enabled: true, command: 'put_shapes', payload: { shapes } }); } catch { /* ignore */ }
        };
        if (!r.points.length) { showPreview(null); putRefine({ ...r, result: null }); return; }
        setTracking(`${samName}: refining…`);
        try {
            const resp = await fetch('/sg-tools/sam3/refine', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    episode: r.episode, frame: r.frame, mode: r.scratch ? 'scratch' : 'edit',
                    mask: r.scratch ? null : r.mask, points: r.points, labels: r.labels,
                }),
            });
            if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
            const res = await resp.json();
            if (seq !== refineSeq.current || !refineRef.current) return;   // stale response: there was a newer click
            showPreview(res);
            putRefine({ ...refineRef.current, result: res });
            setTracking(res.rle ? `${samName}: preview — Enter to apply, Esc to cancel` : `${samName}: empty mask — change the points`);
        } catch (e: any) {
            if (seq === refineSeq.current) setTracking(`${samName} refine failed: ${String(e?.message || e)}`);
        }
    };

    const startRefine = (): void => {
        // the object is the active one (box/mask on the frame) or the one chosen by a click on a graph node, even without shapes
        const label = activeObj ? activeObj.label : labels.find((l) => l.name === role);
        if (!label || !canvasInstance || !jobInstance || busy) return;
        const episode = meta.episode || task?.name;
        if (!episode) { Modal.error({ title: 'No episode name', content: 'Guide has no sg-meta; cannot locate frames.' }); return; }
        const nameAttr = attrMap(label).name;
        const nodeName = nodes.find((n) => n.role === label.name)?.name || '';
        const pts = activeMask?.points as number[] | undefined;
        putRefine({
            role: label.name, frame, episode, label,
            attributes: activeMask ? { ...activeMask.attributes } : { [nameAttr]: (activeObj && activeObj.attributes[nameAttr]) || nodeName },
            group: activeMask ? activeMask.group : null,
            maskID: activeMask ? activeMask.clientID as number : null,
            mask: pts ? {
                rle: pts.slice(0, -4), left: pts[pts.length - 4], top: pts[pts.length - 3],
                width: pts[pts.length - 2] - pts[pts.length - 4] + 1, height: pts[pts.length - 1] - pts[pts.length - 3] + 1,
            } : null,
            scratch: !activeMask, points: [], labels: [], result: null,
        });
        try { (canvasInstance as any).cancel(); } catch { /* ignore */ }
        try {
            (canvasInstance as any).interact({
                enabled: true, command: 'draw_points',
                settings: {
                    crosshair: false, removalStrategy: 'any', points_type: 'any', appendCursorPositionAsPoint: false,
                    hint: 'Left click — add part, right click — remove part, click a point to delete it. Enter — apply, Esc — cancel',
                },
            });
        } catch (e: any) {   // the canvas is busy (drawing, editing): the mode did not start
            putRefine(null);
            Modal.warning({ title: 'Canvas is busy', content: `Finish or cancel the current action (Esc) and try again. ${String(e?.message || e)}` });
            return;
        }
        setTracking(`${samName}: click on the frame`);
    };

    const stopRefine = (): void => {
        putRefine(null);
        refineSeq.current += 1;
        try { (canvasInstance as any)?.interact({ enabled: false }); } catch { /* ignore */ }
    };

    const applyRefine = async (): Promise<void> => {
        const r = refineRef.current;
        if (!r) return;
        const res = r.result;
        stopRefine();
        if (!res) { setTracking(''); return; }
        if (!res.rle) { setTracking(`${samName}: empty mask — nothing changed`); return; }
        setBusy(true);
        try {
            for (const st of await statesOnFrame(r.frame)) {
                if (st.objectType === ObjectType.SHAPE && (st as any).shapeType === 'mask' && st.label.name === r.role) {
                    await st.delete(r.frame, true);
                }
            }
            await dispatch(createAnnotationsAsync([new core.classes.ObjectState({
                objectType: ObjectType.SHAPE, shapeType: 'mask' as any, label: r.label, frame: r.frame,
                points: [...res.rle, res.left, res.top, res.left + res.width - 1, res.top + res.height - 1],
                attributes: { ...r.attributes }, ...(r.group ? { group: r.group } : {}),
            })]));
            // the box together with the mask; an object without a track gets a new track
            await setTrackBoxes(r.role, r.label, { ...r.attributes }, { [r.frame]: [res.bbox[0], res.bbox[1], res.bbox[2], res.bbox[3]] });
            await dispatch(fetchAnnotationsAsync());
            setTracking(`${samName}: mask refined on frame ${r.frame}`);
        } catch (e: any) {
            Modal.error({ title: `${samName} refine failed`, content: String(e?.message || e) });
            setTracking('');
        } finally { setBusy(false); }
    };

    useEffect(() => {
        if (!refining || !canvasInstance) return undefined;
        const el = (canvasInstance as any).html() as HTMLElement;
        const onInteracted = (e: Event): void => {
            const { shapes, finished } = (e as CustomEvent).detail || {};
            const r = refineRef.current;
            if (finished || !Array.isArray(shapes) || !r) return;
            const pts = shapes.filter((s: any) => s.shapeType === 'points');
            const next = {
                ...r,
                points: pts.map((s: any) => [Math.round(s.points[0]), Math.round(s.points[1])]),
                labels: pts.map((s: any) => (s.type === 'negative' ? 0 : 1)),
            };
            putRefine(next);
            runRefine(next);
        };
        const onCanceled = (): void => { if (refineRef.current) { putRefine(null); refineSeq.current += 1; setTracking(''); } };
        const onKey = (e: KeyboardEvent): void => {
            const tag = (e.target as HTMLElement)?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') return;
            if (e.key === 'Enter' || e.key === 'n' || e.key === 'N') { e.preventDefault(); e.stopPropagation(); applyRefine(); }
        };
        el.addEventListener('canvas.interacted', onInteracted);
        el.addEventListener('canvas.canceled', onCanceled);
        window.addEventListener('keydown', onKey, true);
        return () => {
            el.removeEventListener('canvas.interacted', onInteracted);
            el.removeEventListener('canvas.canceled', onCanceled);
            window.removeEventListener('keydown', onKey, true);
        };
    }, [refining !== null, canvasInstance]);

    // left the frame: the refinement of that frame is cancelled
    useEffect(() => { if (refineRef.current && refineRef.current.frame !== frame) { stopRefine(); setTracking(''); } }, [frame]);

    const switchRefineMode = (scratch: boolean): void => {
        const r = refineRef.current;
        if (!r || (!scratch && !r.mask)) return;
        const next = { ...r, scratch };
        putRefine(next);
        runRefine(next);
    };

    // Ctrl+Q (by physical key, so it works with any keyboard layout; fallback Ctrl+Enter): Verified and straight to the
    // next episode; not in input fields and not while marking segments / refining a mask
    useEffect(() => {
        const onKey = (e: KeyboardEvent): void => {
            if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey || !(e.code === 'KeyQ' || e.key === 'Enter')) return;
            const tag = (e.target as HTMLElement)?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') return;
            if (busy || paint || refineRef.current || !jobInstance) return;
            e.preventDefault(); e.stopPropagation();
            setEpisodeVerdict('verified');
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [busy, paint, jobInstance, task, meta]);

    // ---- uniting two roles into one object
    const [uniting, setUniting] = useState(false);
    const [uniteFirst, setUniteFirst] = useState<string | null>(null);
    const [uniteConfirm, setUniteConfirm] = useState<{ keep: string; drop: string } | null>(null);

    const saveMeta = async (next: Meta): Promise<void> => {
        if (!task) return;
        const g = await task.guide();
        if (!g) return;
        const md: string = g.markdown;
        const m = md.match(/<!--\s*sg-meta\s+(\{[\s\S]*?\})\s*-->/);
        const block = `<!-- sg-meta ${JSON.stringify(next)} -->`;
        g.markdown = m ? md.replace(m[0], block) : `${md}\n${block}\n`;
        await g.save();
        setMeta(next);
    };

    const uniteRoles = async (keep: string, drop: string): Promise<void> => {
        if (!jobInstance || keep === drop) return;
        const labelKeep = labels.find((l) => l.name === keep);
        if (!labelKeep) return;
        setBusy(true); setTracking(`uniting ${drop} → ${keep}…`);
        try {
            const keepName = nodes.find((n) => n.role === keep)?.name || nodes.find((n) => n.role === drop)?.name || '';
            const nameId = attrMap(labelKeep).name;
            let keepTrackSeen = false; let dropTrackHandled = false;
            const relabel = async (st: ObjectState, f: number): Promise<void> => {
                st.label = labelKeep; st.attributes = { [nameId]: keepName };
                await st.save();
            };
            for (let f = jobInstance.startFrame; f <= jobInstance.stopFrame; f++) {
                const sts = await statesOnFrame(f);
                const isMask = (x: ObjectState): boolean => x.objectType === ObjectType.SHAPE && (x as any).shapeType === 'mask';
                const keepMask = sts.find((x) => isMask(x) && x.label.name === keep);
                const dropMask = sts.find((x) => isMask(x) && x.label.name === drop);
                if (dropMask) {
                    if (keepMask) await dropMask.delete(f, true);
                    else await relabel(dropMask, f);
                }
                const keepTrack = sts.find((x) => x.objectType === ObjectType.TRACK && x.label.name === keep);
                const dropTrack = sts.find((x) => x.objectType === ObjectType.TRACK && x.label.name === drop);
                if (keepTrack) keepTrackSeen = true;
                if (dropTrack && !dropTrackHandled) {
                    // one track for all frames: decide once
                    const anyKeep = keepTrackSeen || (await (async () => {
                        for (let g = f; g <= jobInstance.stopFrame; g++) {
                            if ((await statesOnFrame(g)).some((x) => x.objectType === ObjectType.TRACK && x.label.name === keep)) return true;
                        }
                        return false;
                    })());
                    if (anyKeep) await dropTrack.delete(f, true);
                    else await relabel(dropTrack, f);
                    dropTrackHandled = true;
                }
                // edges: drop -> keep; loops and duplicates are removed
                const seen = new Set<string>();
                for (const st of sts) {
                    if (st.objectType !== ObjectType.TAG) continue;
                    const e = readEdge(st); if (!e) continue;
                    const from = e.from === drop ? keep : e.from;
                    const to = e.to === drop ? keep : e.to;
                    const key = `${e.kind}|${from}|${to}|${e.value}`;
                    if ((from === to && e.to !== 'none') || seen.has(key)) { await st.delete(f, true); continue; }
                    seen.add(key);
                    if (from !== e.from || to !== e.to) {
                        const ids = attrMap(st.label); const [ff, , tt] = KIND_FIELDS[e.kind];
                        st.attributes = { [ids[ff]]: from, [ids[tt]]: to };
                        await st.save();
                    }
                }
            }
            await dispatch(fetchAnnotationsAsync());
            const merges = { ...(meta.merges || {}) };
            Object.keys(merges).forEach((r) => { if (merges[r] === drop) merges[r] = keep; });
            merges[drop] = keep;
            await saveMeta({ ...meta, merges });
            setTracking(`united: ${roleTitle(keep)}`);
        } catch (e: any) {
            Modal.error({ title: 'Unite failed', content: String(e?.message || e) });
            setTracking('');
        } finally { setBusy(false); setUniting(false); setUniteFirst(null); setUniteConfirm(null); }
    };

    const onNodeClick = (role: string): void => {
        setSelected(null);
        if (uniting) {
            if (uniteFirst === null) { setUniteFirst(role); return; }
            if (uniteFirst !== role) setUniteConfirm({ keep: uniteFirst, drop: role });
            return;
        }
        // outside edge modes a click on a node selects the object for the mask tools, also when it has
        // neither a box nor a mask on the frame (an object without masks in the whole episode): the mask can be created with Refine
        if (!adding) { setPinnedRole(role); return; }
        if (pendingFrom === null) { setPendingFrom(role); return; }
        setNewFromTo([pendingFrom, role]);   // a second click on the same node = unary relation (loop)
        setRange([frame, jobInstance?.stopFrame ?? frame]);
    };

    const boxToolButtons = (): JSX.Element => (
        <>
            <span>frames</span>
            <InputNumber size='small' min={jobInstance?.startFrame ?? 0} max={jobInstance?.stopFrame ?? 0} value={boxRange[0]} onChange={(v) => setBoxRange([Number(v ?? 0), boxRange[1]])} />
            <span>–</span>
            <InputNumber size='small' min={jobInstance?.startFrame ?? 0} max={jobInstance?.stopFrame ?? 0} value={boxRange[1]} onChange={(v) => setBoxRange([boxRange[0], Number(v ?? 0)])} />
            {activeTrack && <Button size='small' disabled={busy} onClick={interpolateBox} title='Keep the boxes on the first and last frame of the range as they are and rebuild the frames in between linearly'>Interpolate</Button>}
            {activeTrack && <Button size='small' disabled={busy} onClick={copyBox} title='Put the box of the current frame on every frame of the range'>Copy this box</Button>}
            {activeMask && <Button size='small' disabled={busy} onClick={copyMask} title='Put the mask of the current frame on every frame of the range (replaces the masks of this object there)'>Copy this mask</Button>}
            {samAvailable && activeMask && <Button size='small' type='primary' disabled={busy} onClick={trackMask} title={`${samName}: track this mask from the current frame to the end of the range; masks and boxes of this object there are replaced`}>{`Track with ${samName}`}</Button>}
            {samAvailable && samRefine && !refining && <Button size='small' disabled={busy} onClick={startRefine} title={`${samName}: fix the mask of this object on the current frame by clicks — left click adds a part, right click removes a part`}>{`Refine with ${samName}`}</Button>}
            {samAvailable && <Button size='small' disabled={busy} onClick={() => masksFromBoxes(true)} title={`${samName}: on frames of the range where this object has a box but no mask, build the mask from the box`}>Masks from boxes</Button>}
            {activeTrack && (activeTrack.outside
                ? <Button size='small' disabled={busy} onClick={() => setOutside(false)} title='The object is back in the frame: clear the outside flag on the range'>Show box</Button>
                : <Button size='small' danger disabled={busy} onClick={() => setOutside(true)} title='The object left the frame: mark the box as outside on the range (the track is kept)'>Hide box</Button>)}
            <Button size='small' danger disabled={busy} onClick={deleteMasks} title='Delete the masks of this object on the range'>Delete masks</Button>
            {!samAvailable && <span className='sg-hint-inline'>tracking service not connected: SAM buttons are hidden</span>}
        </>
    );

    const scopePicker = (withRun: boolean): JSX.Element => (
        <>
            <Radio.Group size='small' value={scope === 'run' && !withRun ? 'all' : scope} onChange={(e) => setScope(e.target.value)} className='sg-scope'>
                <Radio.Button value='frame' title='only the current frame'>this frame</Radio.Button>
                {withRun && <Radio.Button value='run' title='current frame and all neighbouring frames where this same edge continues without a break'>this run</Radio.Button>}
                <Radio.Button value='all' title='every frame of the episode'>all frames</Radio.Button>
                <Radio.Button value='range' title='frames from … to … (inclusive)'>frames</Radio.Button>
            </Radio.Group>
            {scope === 'range' && (
                <div className='sg-row sg-range'>
                    <span>from</span>
                    <InputNumber size='small' min={jobInstance?.startFrame ?? 0} max={jobInstance?.stopFrame ?? 0} value={range[0]} onChange={(v) => setRange([Number(v ?? 0), range[1]])} />
                    <span>to</span>
                    <InputNumber size='small' min={jobInstance?.startFrame ?? 0} max={jobInstance?.stopFrame ?? 0} value={range[1]} onChange={(v) => setRange([range[0], Number(v ?? 0)])} />
                </div>
            )}
        </>
    );

    const valueInput = (kind: Kind, value: string, onChange: (v: string) => void, onEnter: () => void): JSX.Element => (
        <AutoComplete
            size='small'
            value={value}
            options={suggestions(kind).map((v) => ({ value: v }))}
            onChange={onChange}
            onKeyDown={(e) => { if (e.key === 'Enter') onEnter(); }}
            placeholder={`${kind} (choose or type)`}
            filterOption={(inp, opt) => (opt?.value as string).toLowerCase().includes(inp.toLowerCase())}
        />
    );

    const editPopover = selected && (
        <div className='sg-popover'>
            <Text strong>{selected.to === 'none' || selected.to === selected.from ? `${selected.from} (self)` : `${selected.from} → ${selected.to}`}</Text>
            {valueInput(selected.kind, editValue, setEditValue, () => changeEdge(selected, editValue, scope))}
            {scopePicker(true)}
            <div className='sg-row'>
                <Button size='small' type='primary' disabled={busy || !editValue || editValue === selected.value} onClick={() => changeEdge(selected, editValue, scope)}>Apply</Button>
                <Button size='small' danger disabled={busy} onClick={() => deleteEdge(selected, scope)}>Delete edge</Button>
                <Button size='small' onClick={() => setSelected(null)}>Cancel</Button>
            </div>
        </div>
    );

    const newPopover = newFromTo && adding && (
        <div className='sg-popover'>
            <Text strong>{newFromTo[0] === newFromTo[1] ? `${newFromTo[0]} (self)` : `${newFromTo[0]} → ${newFromTo[1]}`}</Text>
            {valueInput(adding, newValue, setNewValue, () => addEdge(adding, newFromTo[0], newFromTo[1] === newFromTo[0] ? 'none' : newFromTo[1], newValue, scope))}
            {scopePicker(false)}
            <div className='sg-row'>
                <Button size='small' type='primary' disabled={busy || !newValue} onClick={() => addEdge(adding, newFromTo[0], newFromTo[1] === newFromTo[0] ? 'none' : newFromTo[1], newValue, scope)}>Add</Button>
                <Button size='small' onClick={() => { setAdding(null); setPendingFrom(null); setNewFromTo(null); }}>Cancel</Button>
            </div>
        </div>
    );

    // ---- rendering
    const NW = (n: Node): number => Math.max(96, 8 * Math.max(n.name.length, roleTitle(n.role).length * 0.8) + 20);
    const NH = 40;
    const isSel = (e: Edge): boolean => !!selected && selected.state.clientID === e.state.clientID;
    const labelBox = (x: number, y: number, text: string, sel: boolean, key: string, onClick: () => void): JSX.Element => {
        const w = Math.max(44, 7 * text.length + 12);
        return (
            <g key={key} className={`sg-edge ${sel ? 'sg-edge-selected' : ''}`} onClick={onClick}>
                <rect x={x - w / 2} y={y - 9} width={w} height={18} rx={4} fill='#fff' stroke={sel ? '#1890ff' : '#999'} strokeWidth={sel ? 2 : 1} />
                <text x={x} y={y + 4} textAnchor='middle' fontSize={11}>{text}</text>
            </g>
        );
    };
    const ext = { minX: Infinity, maxX: -Infinity, minY: 0, maxY: H };   // extent of the graph including arcs, to fit it into the panel
    const grow = (x: number, y: number, pad = 34): void => {
        ext.minX = Math.min(ext.minX, x - pad); ext.maxX = Math.max(ext.maxX, x + pad);
        ext.minY = Math.min(ext.minY, y - 12); ext.maxY = Math.max(ext.maxY, y + 12);
    };
    const pick = (e: Edge): void => { setAdding(null); setPendingFrom(null); setNewFromTo(null); setSelected(e); setEditValue(e.value); setRange([frame, jobInstance?.stopFrame ?? frame]); };

    const edgeElems = edges.map((e, i) => {
        const a = pos[e.from];
        if (!a) return null;
        const stroke = e.kind === 'relation' ? '#555' : '#1f1f1f';
        const dash = e.kind === 'relation' ? '6,4' : undefined;   // relation = dashed, action = solid
        const sw = isSel(e) ? 3 : 1.5;
        const key = `e${i}`;
        if (e.to === 'none' || e.to === e.from) {
            // a loop from the right edge of the node back to it; several loops = nested arcs of different sizes
            const loops = edges.filter((o) => o.from === e.from && (o.to === 'none' || o.to === o.from));
            const j = loops.indexOf(e);
            const w = NW(nodes.find((n) => n.role === e.from) as Node) / 2;
            const r = 60 + 30 * j;
            const d = `M${a.x + w},${a.y - 8 - 4 * j} C${a.x + w + r},${a.y - r * 0.85} ${a.x + w + r},${a.y + r * 0.85} ${a.x + w},${a.y + 8 + 4 * j}`;
            // loop labels: a column to the right of the node, one per line
            const ly = a.y - 11 * (loops.length - 1) + 22 * j;
            const lx = a.x + w + 40 + 4 * e.value.length;
            grow(lx, ly, 4 * e.value.length + 12);
            return (
                <g key={key}>
                    <path d={d} fill='none' stroke={stroke} strokeWidth={sw} strokeDasharray={dash} markerEnd='url(#sg-arrow)' />
                    <path d={d} fill='none' stroke='transparent' strokeWidth={14} className='sg-edge' onClick={() => pick(e)} />
                    {labelBox(lx, ly, e.value, isSel(e), `${key}l`, () => pick(e))}
                </g>
            );
        }
        const b = pos[e.to];
        if (!b) return null;
        const siblings = edges.filter((o) => (o.from === e.from && o.to === e.to) || (o.from === e.to && o.to === e.from));
        const k = siblings.indexOf(e) - (siblings.length - 1) / 2;
        const dx = b.x - a.x; const dy = b.y - a.y; const len = Math.hypot(dx, dy) || 1;
        // the normal is taken in the canonical order of the pair, otherwise opposite edges fall on the same side
        const fwd = e.from < e.to ? 1 : -1;
        const nx = (-dy / len) * fwd; const ny = (dx / len) * fwd;
        // another node on the straight line (an edge across a row): go around it with a side arc
        let detour = 0;
        nodes.forEach((n) => {
            if (n.role === e.from || n.role === e.to) return;
            const p = pos[n.role]; const t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (len * len);
            if (t <= 0.05 || t >= 0.95) return;
            const dist = Math.abs((p.x - a.x) * nx + (p.y - a.y) * ny);
            if (dist < NW(n) / 2 + 20) detour = Math.max(detour, NW(n) / 2 + 60);
        });
        const off = 110 * k + (detour ? detour * 2 * (k >= 0 ? 1 : -1) : 0);   // x2: the apex of a quadratic curve is halfway to the control point
        const cx = (a.x + b.x) / 2 + nx * off; const cy = (a.y + b.y) / 2 + ny * off;
        // cut to the borders of the rectangles
        const cut = (p: { x: number; y: number }, w: number, tx: number, ty: number) => {
            const vx = tx - p.x; const vy = ty - p.y;
            const s = Math.min(Math.abs((w / 2 + 3) / (vx || 1e-6)), Math.abs((NH / 2 + 3) / (vy || 1e-6)));
            return { x: p.x + vx * s, y: p.y + vy * s };
        };
        const sa = cut(a, NW(nodes.find((n) => n.role === e.from) as Node), cx, cy);
        const sb = cut(b, NW(nodes.find((n) => n.role === e.to) as Node), cx, cy);
        const d = `M${sa.x},${sa.y} Q${cx},${cy} ${sb.x},${sb.y}`;
        const lx = 0.25 * sa.x + 0.5 * cx + 0.25 * sb.x; const ly = 0.25 * sa.y + 0.5 * cy + 0.25 * sb.y;
        grow(lx, ly, Math.max(30, 3.5 * e.value.length + 8));
        return (
            <g key={key}>
                <path d={d} fill='none' stroke={stroke} strokeWidth={sw} strokeDasharray={dash} markerEnd='url(#sg-arrow)' />
                <path d={d} fill='none' stroke='transparent' strokeWidth={14} className='sg-edge' onClick={() => pick(e)} />
                {labelBox(lx, ly, e.value, isSel(e), `${key}l`, () => pick(e))}
            </g>
        );
    });

    nodes.forEach((n) => grow(pos[n.role].x, pos[n.role].y, NW(n) / 2 + 4));
    const vbW = ext.maxX - ext.minX; const vbH = ext.maxY - ext.minY;
    const scale = Math.min(1.25, W / vbW);   // the graph grows with the panel, but not more than x1.25
    const nodeElems = nodes.map((n) => {
        const p = pos[n.role]; const w = NW(n);
        const pending = pendingFrom === n.role;
        return (
            <g key={n.role} className={`sg-node ${adding ? 'sg-node-pickable' : ''}`} onClick={() => onNodeClick(n.role)} onDoubleClick={() => { if (!adding) setRename({ role: n.role, name: n.name }); }}>
                <rect x={p.x - w / 2} y={p.y - NH / 2} width={w} height={NH} rx={8} fill={n.color} fillOpacity={visibleRoles.has(n.role) ? 0.92 : 0.3} stroke={pending ? '#1890ff' : '#333'} strokeWidth={pending ? 3 : 1.2} strokeDasharray={visibleRoles.has(n.role) ? undefined : '4,3'} />
                <text x={p.x} y={p.y - 3} textAnchor='middle' fontSize={12} fill='#fff' fontWeight={700}>{n.name || '?'}</text>
                <text x={p.x} y={p.y + 12} textAnchor='middle' fontSize={10} fill='#fff' opacity={0.9}>{roleTitle(n.role)}</text>
            </g>
        );
    });

    const hint = uniting
        ? (uniteFirst ? `keep "${uniteFirst}": now click the node that is the same object` : 'Unite: click the node to KEEP (its name stays)')
        : adding
            ? (pendingFrom ? `${pendingFrom} → click the target node (same node = self relation)` : `Add ${adding}: click the source node`)
            : 'click an edge to edit · double-click a node to rename';

    const pinnedIDs = [activeTrack?.clientID, activeMask?.clientID].filter((x) => typeof x === 'number');
    // "as on hover, but weaker": on hover CVAT raises the fill-opacity of the shape
    // from shapes.opacity to shapes.selectedOpacity (for masks, their square roots). We take the middle.
    const midOpacity = (shapeOpacity + selectedOpacity) / 2 / 100;

    return (
        <Layout.Sider className='sg-sidebar' theme='light' width={panelW} collapsedWidth={0} collapsible trigger={null} collapsed={collapsed}>
            {!collapsed && <div className='sg-resizer' onMouseDown={startResize} title='drag to resize' />}
            <span className='sg-sidebar-toggle' onClick={() => setCollapsed(!collapsed)}>
                {collapsed ? <MenuUnfoldOutlined title='Show graph' /> : <MenuFoldOutlined title='Hide graph' />}
            </span>
            {/* The pinned object is slightly highlighted all the time. The rule is off when CVAT
                highlights the shape itself (hover, selection), so the standard highlights work as before. */}
            {!!pinnedIDs.length && (
                <style>
                    {pinnedIDs.map((id) => `#cvat_canvas_shape_${id}:not(.cvat_canvas_shape_activated)`).join(',')}
                    {` { fill-opacity: ${midOpacity.toFixed(3)}; }`}
                    {pinnedIDs.map((id) => `image#cvat_canvas_shape_${id}:not(.cvat_canvas_shape_activated)`).join(',')}
                    {` { opacity: ${Math.sqrt(midOpacity).toFixed(3)}; }`}
                </style>
            )}
            {/* while refining by clicks the old mask is barely visible, and hidden when a preview is ready */}
            {refining && refining.maskID !== null && (
                <style>{`#cvat_canvas_shape_${refining.maskID} { opacity: ${refining.result?.rle ? 0 : 0.35} !important; }`}</style>
            )}
            <div className='sg-header'>
                <div>
                    <Text type='secondary'>Goal:</Text> <Text strong>{meta.goal || '—'}</Text>
                    <Button size='small' type='link' className='sg-unmark' disabled={busy || !task} onClick={() => setGoalEdit(meta.goal || '')} title='Edit the goal of this episode'>edit</Button>
                    {verdict === 'verified' && <span className='sg-badge sg-badge-ok'>verified</span>}
                    {verdict === 'goal_mismatch' && <span className='sg-badge sg-badge-bad'>goal mismatch</span>}
                    {verdict && <Button size='small' type='link' className='sg-unmark' disabled={busy} onClick={clearVerdict} title='Remove the verdict; the episode becomes "not checked" again'>unmark</Button>}
                </div>
                <div className='sg-roles'>
                    {nodes.map((n) => (
                        <span key={n.role}><span className='sg-dot' style={{ background: n.color }} /><b>{n.name || '?'}</b>{` (${roleTitle(n.role)})`}</span>
                    ))}
                </div>
            </div>
            <div className='sg-toolbar'>
                {relationLabel && <Button size='small' type={adding === 'relation' ? 'primary' : 'default'} disabled={busy} onClick={() => { setSelected(null); setPendingFrom(null); setNewFromTo(null); setAdding(adding === 'relation' ? null : 'relation'); }}>Add relation</Button>}
                {actionLabel && <Button size='small' type={adding === 'action' ? 'primary' : 'default'} disabled={busy} onClick={() => { setSelected(null); setPendingFrom(null); setNewFromTo(null); setAdding(adding === 'action' ? null : 'action'); }}>Add action</Button>}
                <Button size='small' type={uniting ? 'primary' : 'default'} disabled={busy || nodes.length < 2} onClick={() => { setSelected(null); setAdding(null); setPendingFrom(null); setNewFromTo(null); setUniteFirst(null); setUniting(!uniting); }} title='Two nodes are the same object: click the node to keep, then the node to merge into it'>Unite objects</Button>
                <Button size='small' danger icon={<CloseCircleOutlined />} className='sg-mismatch' disabled={busy} onClick={() => setEpisodeVerdict('goal_mismatch')} title='The video does not match its goal — set the episode aside'>Goal mismatch</Button>
                <Button size='small' icon={<CheckOutlined />} className='sg-verify' disabled={busy} onClick={() => setEpisodeVerdict('verified')} title='Mark as verified and open the next episode (Ctrl+Q)'>Verified</Button>
            </div>
            <div className='sg-hint'>{hint}</div>
            <div className='sg-boxtools'>
                <span className='sg-boxtools-title'>
                    {activeObj ? (
                        <>
                            <span className='sg-dot' style={{ background: activeObj.label.color }} />
                            {`${activeObj.attributes[attrMap(activeObj.label).name] || activeObj.label.name} (${role}${activeMask ? ', mask' : ''}${activeTrack ? ', box' : ''})`}
                            <Button size='small' type='link' className='sg-unmark' onClick={() => setPinnedRole(null)} title='Unpin this object'>×</Button>
                        </>
                    ) : role ? (
                        <>
                            <span className='sg-dot' style={{ background: labels.find((l) => l.name === role)?.color }} />
                            {`${nodes.find((n) => n.role === role)?.name || role} (${role}) — no box or mask on this frame`}
                            <Button size='small' type='link' className='sg-unmark' onClick={() => setPinnedRole(null)} title='Unpin this object'>×</Button>
                        </>
                    ) : 'Box / mask tools: click an object on the frame or a node of the graph'}
                </span>
                {activeObj && <div className='sg-row sg-range'>{boxToolButtons()}</div>}
                {!activeObj && role && samAvailable && samRefine && !refining && (
                    <div className='sg-row sg-range'>
                        <Button size='small' type='primary' disabled={busy} onClick={startRefine} title={`${samName}: create the mask of this object on the current frame by clicks on it`}>{`Refine with ${samName}`}</Button>
                        <span>create the mask by clicks, then Track</span>
                    </div>
                )}
                {refining && (
                    <div className='sg-row sg-range sg-refine'>
                        <b>{`Refine ${refining.role}, frame ${refining.frame}:`}</b>
                        <Radio.Group size='small' value={refining.scratch ? 'scratch' : 'edit'} onChange={(e) => switchRefineMode(e.target.value === 'scratch')}>
                            <Radio.Button value='edit' disabled={!refining.mask} title='Each click adds / removes the part under it; the rest of the mask stays'>edit parts</Radio.Button>
                            <Radio.Button value='scratch' title='Ignore the current mask: the object is segmented from the points only'>from scratch</Radio.Button>
                        </Radio.Group>
                        <Button size='small' type='primary' disabled={!refining.result?.rle} onClick={applyRefine}>Apply (Enter)</Button>
                        <Button size='small' onClick={() => { stopRefine(); setTracking(''); }}>Cancel (Esc)</Button>
                    </div>
                )}
                {tracking && <div className='sg-tracking'>{tracking}</div>}
            </div>
            <Popover open={!!selected} content={editPopover} placement='right'>
                <Popover open={!!newFromTo} content={newPopover} placement='right'>
                    <svg className='sg-svg' width={vbW * scale} height={vbH * scale} viewBox={`${ext.minX} ${ext.minY} ${vbW} ${vbH}`}>
                        <defs>
                            <marker id='sg-arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' markerHeight='7' orient='auto'>
                                <path d='M0,0 L10,5 L0,10 z' fill='#1f1f1f' />
                            </marker>
                        </defs>
                        {edgeElems}
                        {nodeElems}
                    </svg>
                </Popover>
            </Popover>
            <div className='sg-frame'>{`frame ${frame} · ${edges.length} edges · dashed = relation, solid = action`}</div>

            <div className='sg-timeline' style={{ zoom: Math.min(1.3, panelW / 380) } as React.CSSProperties}>
                {(() => {
                    if (!jobInstance || !tlRows.length) return null;
                    const f0 = jobInstance.startFrame; const nF = jobInstance.stopFrame - f0 + 1;
                    const nameOf = (r: string): string => (r === 'none' ? '' : (nodes.find((n) => n.role === r)?.name || r));
                    const colorOf = (r: string): string => (nodes.find((n) => n.role === r)?.color || '#888');
                    return (
                        <>
                            <div className='sg-tl-head'><span>relation / action over frames</span><span>{`frames ${f0}–${jobInstance.stopFrame}`}</span></div>
                            {tlRows.map((r) => {
                                const pair = r.to === 'none' || r.to === r.from ? nameOf(r.from) : `${nameOf(r.from)} → ${nameOf(r.to)}`;
                                const editing = !!paint && paint.kind === r.kind && paint.from === r.from
                                    && paint.to === r.to && paint.value === r.value;
                                const painted = editing ? paintFrames(paint as Paint) : null;
                                return (
                                    <div className={`sg-tl-row ${editing ? 'sg-tl-editing' : ''}`} key={r.key} title={`${r.kind} ${r.value}: ${pair}`}>
                                        <div
                                            className='sg-tl-label sg-tl-label-click'
                                            onClick={() => setPaint(editing ? null : {
                                                kind: r.kind, from: r.from, to: r.to, value: r.value, starts: [], ends: [],
                                            })}
                                            title='click to re-mark the frames of this edge'
                                        >
                                            <span className='sg-dot' style={{ background: colorOf(r.from) }} />
                                            <span className='sg-tl-text'>
                                                <b className={r.kind === 'action' ? 'sg-tl-action' : ''}>{r.value}</b>
                                                <small>{pair}</small>
                                            </span>
                                            {editing && (
                                                <Button size='small' type='link' className='sg-tl-done' disabled={busy} onClick={(e) => { e.stopPropagation(); applyPaint(); }} title='apply (N)'>✓</Button>
                                            )}
                                        </div>
                                        <div className='sg-tl-bar'>
                                            {Array.from({ length: nF }, (_, i) => f0 + i).map((f) => {
                                                const on = editing ? (painted as Set<number>).has(f) : r.frames.has(f);
                                                const isStart = editing && (paint as Paint).starts.includes(f);
                                                const isEnd = editing && (paint as Paint).ends.includes(f);
                                                return (
                                                    <div
                                                        key={f}
                                                        className={`sg-tl-cell ${on ? 'on' : ''} ${f === frame ? 'cur' : ''} ${isStart ? 'mark-start' : ''} ${isEnd ? 'mark-end' : ''}`}
                                                        style={on ? { background: colorOf(r.from), opacity: r.kind === 'action' ? 1 : 0.55 } : undefined}
                                                        onMouseEnter={() => { if (editing && f !== frame) dispatch(changeFrameAsync(f)); }}
                                                        onClick={() => {
                                                            if (!editing) { dispatch(changeFrameAsync(f)); return; }
                                                            setPaint(toggleMark(paint as Paint, f, true));
                                                        }}
                                                        onContextMenu={(e) => {
                                                            if (!editing) return;
                                                            e.preventDefault(); e.stopPropagation();
                                                            setPaint(toggleMark(paint as Paint, f, false));
                                                        }}
                                                        title={editing ? `frame ${f}: left click = start, right click = end` : `frame ${f}`}
                                                    />
                                                );
                                            })}
                                        </div>
                                    </div>
                                );
                            })}
                            <div className='sg-tl-row sg-tl-addrow'>
                                <div className='sg-tl-label'>
                                    <Button size='small' type='link' disabled={busy || !!paint} onClick={() => setNewEdge({ kind: 'relation', from: nodes[0]?.role || '', to: 'none', value: '' })}>+ new relation / action</Button>
                                </div>
                                <div className='sg-tl-bar' />
                            </div>
                            {paint && (
                                <div className='sg-tl-hint'>
                                    <div className='sg-tl-hint-buttons'>
                                        <Button size='small' type='primary' disabled={busy} onClick={applyPaint}>✓ Apply (N)</Button>
                                        <Button size='small' disabled={busy} onClick={() => setPaint(null)}>Cancel (Esc)</Button>
                                    </div>
                                    {`marking "${paint.value}": left click = start of a segment, right click = end, click again = remove the mark, hover moves the frame`}
                                </div>
                            )}
                            <div className='sg-tl-row sg-tl-axis'>
                                <div className='sg-tl-label' />
                                <div className='sg-tl-bar'>
                                    {Array.from({ length: nF }, (_, i) => f0 + i).map((f) => (
                                        <div key={f} className={`sg-tl-tick ${f === frame ? 'cur' : ''}`} onClick={() => dispatch(changeFrameAsync(f))}>{f % 5 === 0 && <span>{f}</span>}</div>
                                    ))}
                                </div>
                            </div>
                        </>
                    );
                })()}
            </div>
            <Modal
                open={!!newEdge}
                title='New relation / action'
                okText='Mark frames'
                onOk={() => {
                    if (!newEdge || !newEdge.value || !newEdge.from) return;
                    setPaint({ ...newEdge, starts: [], ends: [] });
                    setNewEdge(null);
                }}
                onCancel={() => setNewEdge(null)}
                destroyOnClose
            >
                {newEdge && (
                    <div className='sg-popover'>
                        <Radio.Group size='small' value={newEdge.kind} onChange={(e) => setNewEdge({ ...newEdge, kind: e.target.value, value: '' })}>
                            <Radio.Button value='relation'>relation</Radio.Button>
                            <Radio.Button value='action'>action</Radio.Button>
                        </Radio.Group>
                        <div className='sg-row'>
                            <Select
                                size='small'
                                style={{ minWidth: 150 }}
                                value={newEdge.from}
                                options={nodes.map((n) => ({ value: n.role, label: `${n.name || n.role} (${n.role})` }))}
                                onChange={(v) => setNewEdge({ ...newEdge, from: v })}
                            />
                            <span>→</span>
                            <Select
                                size='small'
                                style={{ minWidth: 150 }}
                                value={newEdge.to}
                                options={[{ value: 'none', label: '— (state of one object)' },
                                    ...nodes.map((n) => ({ value: n.role, label: `${n.name || n.role} (${n.role})` }))]}
                                onChange={(v) => setNewEdge({ ...newEdge, to: v })}
                            />
                        </div>
                        {valueInput(newEdge.kind, newEdge.value, (v) => setNewEdge({ ...newEdge, value: v }), () => {})}
                    </div>
                )}
            </Modal>
            <Modal
                open={!!uniteConfirm}
                title='Unite two objects'
                okText='Unite'
                confirmLoading={busy}
                onOk={() => uniteConfirm && uniteRoles(uniteConfirm.keep, uniteConfirm.drop)}
                onCancel={() => { setUniteConfirm(null); setUniteFirst(null); setUniting(false); }}
            >
                {uniteConfirm && (
                    <div>
                        <p><b>{roleTitle(uniteConfirm.keep)}</b> and <b>{roleTitle(uniteConfirm.drop)}</b> become one object <b>{[roleTitle(uniteConfirm.keep), uniteConfirm.drop].join(' & ')}</b>.</p>
                        <p>Boxes and masks stay with <b>{uniteConfirm.keep}</b>; where it has none, those of {uniteConfirm.drop} are used. All edges of {uniteConfirm.drop} are re-pointed to {uniteConfirm.keep}; edges between the two are removed.</p>
                        <p>Annotation changes can be undone with Ctrl+Z; the merge record in the task guide is kept.</p>
                    </div>
                )}
            </Modal>
            <Modal
                open={!!rename}
                title={rename ? `Rename ${rename.role}` : ''}
                okText='Rename on all frames'
                confirmLoading={busy}
                onOk={() => rename && renameRole(rename.role, rename.name.trim())}
                onCancel={() => setRename(null)}
                destroyOnClose
            >
                <Input autoFocus value={rename?.name || ''} onChange={(e) => rename && setRename({ ...rename, name: e.target.value })} onPressEnter={() => rename && renameRole(rename.role, rename.name.trim())} />
            </Modal>
            <Modal
                open={goalEdit !== null}
                title='Edit goal'
                okText='Save goal'
                confirmLoading={busy}
                onOk={async () => {
                    if (goalEdit === null) return;
                    setBusy(true);
                    try { await saveMeta({ ...meta, goal: goalEdit.trim() }); setGoalEdit(null); } finally { setBusy(false); }
                }}
                onCancel={() => setGoalEdit(null)}
                destroyOnClose
            >
                <Input.TextArea autoFocus autoSize={{ minRows: 2, maxRows: 6 }} value={goalEdit || ''} onChange={(e) => setGoalEdit(e.target.value)} />
            </Modal>
        </Layout.Sider>
    );
}
