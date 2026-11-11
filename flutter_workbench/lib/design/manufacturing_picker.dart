import 'dart:math' as math;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'canvas.dart';
import 'controller.dart';

const pickingNames = {
  'start': 'Pick profile start',
  'tabs': 'Place profile tabs',
  'boundaries': 'Pick cleanout boundaries',
  'islands': 'Pick cleanout islands',
  'order': 'Pick contour order',
};

class ManufacturingPicker extends StatefulWidget {
  const ManufacturingPicker({
    super.key,
    required this.model,
    required this.info,
  });
  final DesignController model;
  final Json info;
  @override
  State<ManufacturingPicker> createState() => _ManufacturingPickerState();
}

class _ManufacturingPickerState extends State<ManufacturingPicker> {
  final view = DesignViewport();
  late final revision = widget.model.state['revision'];
  late final op = widget.info['operation'] as Json;
  late final shape = widget.info['shape'] as Json;
  late final kind = widget.info['kind'] as String;
  late final eligible = Set<String>.from(widget.info['eligible_ids']);
  late final paths = {
    for (final e in widget.model.entities) e['id'] as String: entityPath(e),
  };
  late final ids = List<String>.from(
    kind == 'release'
        ? op['part_order'] ?? []
        : kind == 'centers'
        ? op['small_center_cleanout_ids'] ?? []
        : kind == 'boundaries'
        ? op['entity_ids']
        : kind == 'islands'
        ? op['island_entity_ids'] ?? []
        : kind == 'order'
        ? op['manual_contour_order'] ?? []
        : [],
  );
  late final tabs = (widget.info['tab_points'] as List? ?? [])
      .map(point)
      .toList();
  late int index = (op['start_index'] as num? ?? 0).toInt();
  late final width = TextEditingController(text: '${op['tab_width'] ?? .25}');
  late final height = TextEditingController(text: '${op['tab_height'] ?? .05}');
  String? error;
  bool fitted = false, pan = false;
  Offset? down, last;
  bool dragging = false;
  bool get stale =>
      revision != widget.model.state['revision'] ||
      !widget.model.ready ||
      widget.model.busy;
  List<Offset> get starts =>
      (shape['starts'] as List? ?? []).map(point).toList();
  @override
  void initState() {
    super.initState();
    widget.model.addListener(changed);
  }

  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.model.removeListener(changed);
    view.dispose();
    width.dispose();
    height.dispose();
    super.dispose();
  }

  void fit() {
    Rect? area;
    final show = kind == 'start' || kind == 'tabs'
        ? Set<String>.from(op['entity_ids'])
        : eligible;
    for (final id in show) {
      final b = paths[id]?.getBounds();
      if (b != null) area = area?.expandToInclude(b) ?? b;
    }
    // Include cutter compensation, which can extend beyond the source outline.
    for (final p in [...starts, ...(shape['path'] as List? ?? []).map(point)]) {
      final b = Rect.fromPoints(p, p);
      area = area?.expandToInclude(b) ?? b;
    }
    if (shape['circle'] != null) {
      final circle = shape['circle'] as Json;
      final b = Rect.fromCircle(
        center: point(circle['center']),
        radius: number(circle['radius']),
      );
      area = area?.expandToInclude(b) ?? b;
    }
    view.fit(area ?? const Rect.fromLTWH(0, 0, 10, 10));
  }

  Offset? projected(Offset p) {
    if (shape['circle'] != null) {
      final c = shape['circle'] as Json;
      final center = point(c['center']);
      final r = number(c['radius']);
      final d = p - center;
      return d.distance < 1e-12 ? null : center + d / d.distance * r;
    }
    final pts = (shape['path'] as List? ?? []).map(point).toList();
    Offset? best;
    var distance = double.infinity;
    for (var i = 0; i < pts.length; i++) {
      final a = pts[i], d = pts[(i + 1) % pts.length] - a;
      final len = d.distanceSquared;
      if (len == 0) continue;
      final t = (((p - a).dx * d.dx + (p - a).dy * d.dy) / len).clamp(0.0, 1.0);
      final q = a + d * t;
      final delta = (q - p).distance;
      if (delta < distance) {
        distance = delta;
        best = q;
      }
    }
    return best;
  }

  String? hit(Offset p) {
    final tolerance = 10 / view.scale;
    final hits = <({String id, double distance, double area})>[];
    for (final id in eligible) {
      final path = paths[id];
      if (path == null || !path.getBounds().inflate(tolerance).contains(p)) {
        continue;
      }
      var distance = double.infinity;
      for (final metric in path.computeMetrics()) {
        final steps = math.min(
          10000,
          math.max(2, (metric.length / tolerance).ceil()),
        );
        for (var i = 0; i <= steps; i++) {
          final v = metric
              .getTangentForOffset(metric.length * i / steps)
              ?.position;
          if (v != null) distance = math.min(distance, (v - p).distance);
        }
      }
      if (distance <= tolerance || path.contains(p)) {
        final b = path.getBounds();
        hits.add((id: id, distance: distance, area: b.width * b.height));
      }
    }
    hits.sort((a, b) {
      final ae = a.distance <= tolerance, be = b.distance <= tolerance;
      if (ae != be) return ae ? -1 : 1;
      return ae ? a.distance.compareTo(b.distance) : a.area.compareTo(b.area);
    });
    return hits.firstOrNull?.id;
  }

  void pick(Offset screen) {
    if (stale) return;
    final p = view.world(screen);
    setState(() {
      error = null;
      if (kind == 'start') {
        var nearest = -1, distance = 15.0;
        for (var i = 0; i < starts.length; i++) {
          final d = (view.screen(starts[i]) - screen).distance;
          if (d < distance) {
            distance = d;
            nearest = i;
          }
        }
        if (nearest >= 0) {
          index = nearest;
        } else {
          error = 'Click a numbered preferred-start marker.';
        }
      } else if (kind == 'tabs') {
        final remove = tabs.indexWhere(
          (v) => (view.screen(v) - screen).distance < 10,
        );
        if (remove >= 0) {
          tabs.removeAt(remove);
          return;
        }
        final q = projected(p);
        if (q == null || (view.screen(q) - screen).distance > 14) {
          error = 'Click the teal compensated contour to add a tab.';
          return;
        }
        tabs.add(q);
      } else {
        final id = hit(p);
        if (id != null) {
          if (!ids.remove(id)) ids.add(id);
        }
      }
    });
  }

  void submit({bool automatic = false}) {
    if (stale) return;
    if (kind == 'release' &&
        (ids.length != eligible.length || !eligible.containsAll(ids))) {
      setState(
        () =>
            error = 'Pick every physical body exactly once before re-auditing.',
      );
      return;
    }
    final params = <String, dynamic>{
      'id': op['id'],
      'kind': kind,
      'fingerprint': widget.info['fingerprint'],
    };
    if (kind == 'start') {
      params['index'] = index;
    } else if (kind == 'tabs') {
      final w = double.tryParse(width.text), h = double.tryParse(height.text);
      if (w == null ||
          h == null ||
          !w.isFinite ||
          !h.isFinite ||
          w <= 0 ||
          h <= 0 ||
          tabs.isEmpty) {
        setState(
          () => error =
              'Place at least one tab and enter positive finite width/height.',
        );
        return;
      }
      params.addAll({
        'tab_width': w,
        'tab_height': h,
        'points': tabs
            .map((p) => <String, dynamic>{'x': p.dx, 'y': p.dy})
            .toList(),
      });
    } else {
      params['ids'] = automatic ? <String>[] : ids;
      if (kind == 'order') params['automatic'] = automatic;
    }
    Navigator.pop(context, params);
  }

  String caption(String id) {
    final entities = widget.model.entities;
    final i = entities.indexWhere((e) => e['id'] == id);
    if (i < 0) return 'Missing contour';
    final e = entities[i];
    return kind == 'release'
        ? '${i + 1}: ${e['kind']} · $id'
        : '${i + 1}: ${e['kind']} · ${e['layer']}';
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.sizeOf(context);
    return Dialog(
      child: SizedBox(
        width: math.min(1120, size.width - 32),
        height: math.min(760, size.height - 48),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                '${kind == 'release'
                    ? 'Pick final-release order'
                    : kind == 'centers'
                    ? 'Pick small centers'
                    : pickingNames[kind]} — ${op['name']}',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              Text(switch (kind) {
                'release' =>
                  'Click physical bodies in release order. Remove a numbered body and pick it again to append, or Clear picks and start over. Every body is required. Numbers request cleanup order; shared bridges can release bodies together. Re-audit before confirming restraint. Drawing coordinates are unchanged.',
                'centers' =>
                  'Click eligible enclosed centers to clear before this profile. Other contours stay unchanged. Review center-out motion before Apply.',
                'start' =>
                  'Click a numbered start on the compensated contour. The core may adjust entry for clearance or cycle optimization; review the generated motion. Manual tab locations are retained.',
                'tabs' =>
                  'Click the teal compensated contour to add tabs; click a tab to remove it. Width is the finished bridge. Review actual raised motion before Apply.',
                'boundaries' =>
                  'Click outer contours to include/exclude them. Existing islands are retained and must fit within the chosen boundaries.',
                'islands' =>
                  'Click internal closed contours to protect as islands. The selected outer boundaries stay unchanged.',
                _ =>
                  'Click contours in cutting order. Click a numbered contour to remove it from the sequence, then pick it again to append. Every contour is required.',
              }),
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  IconButton(
                    tooltip: 'Fit picker drawing',
                    onPressed: fit,
                    icon: const Icon(Icons.fit_screen),
                  ),
                  FilterChip(
                    label: const Text('Pan'),
                    selected: pan,
                    onSelected: (v) => setState(() => pan = v),
                  ),
                  const Text('Scroll to zoom · middle-drag to pan'),
                  if (kind == 'tabs') ...[
                    SizedBox(
                      width: 130,
                      child: TextField(
                        controller: width,
                        decoration: InputDecoration(
                          labelText: 'Tab width (${widget.model.units})',
                        ),
                      ),
                    ),
                    SizedBox(
                      width: 130,
                      child: TextField(
                        controller: height,
                        decoration: InputDecoration(
                          labelText: 'Tab height (${widget.model.units})',
                        ),
                      ),
                    ),
                  ],
                  if (kind != 'start')
                    TextButton(
                      onPressed: () => setState(() {
                        ids.clear();
                        tabs.clear();
                      }),
                      child: const Text('Clear picks'),
                    ),
                ],
              ),
              if (stale)
                const Text(
                  'The drawing changed. Cancel and reopen this picker.',
                  style: TextStyle(color: Colors.red),
                ),
              if (error != null)
                Text(error!, style: const TextStyle(color: Colors.red)),
              Expanded(
                child: LayoutBuilder(
                  builder: (context, c) {
                    view.size = Size(c.maxWidth, c.maxHeight);
                    if (!fitted) {
                      fitted = true;
                      fit();
                    }
                    return AnimatedBuilder(
                      animation: view,
                      builder: (context, _) => Listener(
                        key: const ValueKey('manufacturing-picker-canvas'),
                        onPointerDown: (e) {
                          down = last = e.localPosition;
                          dragging = pan || e.buttons & kMiddleMouseButton != 0;
                        },
                        onPointerMove: (e) {
                          if (last != null && dragging) {
                            view.translation += e.localPosition - last!;
                            view.changed();
                          }
                          last = e.localPosition;
                        },
                        onPointerUp: (e) {
                          if (down != null &&
                              !dragging &&
                              (e.localPosition - down!).distance < 5) {
                            pick(e.localPosition);
                          }
                          down = last = null;
                          dragging = false;
                        },
                        onPointerCancel: (_) {
                          down = last = null;
                          dragging = false;
                        },
                        onPointerSignal: (e) {
                          if (e is PointerScrollEvent) {
                            GestureBinding.instance.pointerSignalResolver
                                .register(
                                  e,
                                  (_) => view.zoom(
                                    math.exp(-e.scrollDelta.dy * .0015),
                                    e.localPosition,
                                  ),
                                );
                          }
                        },
                        child: ClipRect(
                          child: CustomPaint(
                            painter: DesignPainter(
                              widget.model,
                              view,
                              paths,
                              Theme.of(context).brightness == Brightness.dark,
                            ),
                            foregroundPainter: ManufacturingPickPainter(
                              view,
                              paths,
                              ids,
                              starts,
                              index,
                              tabs,
                              shape,
                              kind,
                              eligible,
                            ),
                            child: const SizedBox.expand(),
                          ),
                        ),
                      ),
                    );
                  },
                ),
              ),
              if (!['start', 'tabs'].contains(kind))
                SizedBox(
                  height: 62,
                  child: SingleChildScrollView(
                    child: Wrap(
                      spacing: 5,
                      children: [
                        for (final id in eligible)
                          FilterChip(
                            label: Text(
                              '${(kind == 'order' || kind == 'release') && ids.contains(id) ? '${ids.indexOf(id) + 1} → ' : ''}${caption(id)}',
                            ),
                            selected: ids.contains(id),
                            onSelected: stale
                                ? null
                                : (v) => setState(() {
                                    if (v) {
                                      ids.add(id);
                                    } else {
                                      ids.remove(id);
                                    }
                                  }),
                          ),
                      ],
                    ),
                  ),
                ),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      kind == 'start'
                          ? 'Preferred start ${index + 1}'
                          : kind == 'tabs'
                          ? '${tabs.length} proposed tabs'
                          : '${ids.length} contours selected',
                    ),
                  ),
                  TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('Cancel'),
                  ),
                  if (kind == 'order')
                    TextButton(
                      onPressed: stale ? null : () => submit(automatic: true),
                      child: const Text('Use automatic order'),
                    ),
                  FilledButton(
                    onPressed: stale ? null : submit,
                    child: Text(
                      kind == 'release'
                          ? 'Re-audit release order'
                          : 'Review picked changes',
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class ManufacturingPickPainter extends CustomPainter {
  ManufacturingPickPainter(
    this.view,
    this.paths,
    this.ids,
    this.starts,
    this.index,
    this.tabs,
    this.shape,
    this.kind,
    this.eligible,
  );
  final DesignViewport view;
  final Map<String, Path> paths;
  final List<String> ids;
  final List<Offset> starts, tabs;
  final int index;
  final Json shape;
  final String kind;
  final Set<String> eligible;
  void label(Canvas canvas, Offset p, String value, Color color) {
    canvas.drawCircle(p, 10, Paint()..color = color);
    final text = TextPainter(
      text: TextSpan(
        text: value,
        style: const TextStyle(
          fontSize: 11,
          color: Colors.white,
          fontFamily: 'Segoe UI',
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    text.paint(canvas, p - Offset(text.width / 2, text.height / 2));
  }

  @override
  void paint(Canvas canvas, Size size) {
    final matrix = Matrix4.identity()
      ..translateByDouble(view.translation.dx, view.translation.dy, 0, 1)
      ..scaleByDouble(view.scale, -view.scale, 1, 1);
    for (final id in eligible) {
      final path = paths[id];
      if (path == null) continue;
      final selected = ids.contains(id);
      canvas.drawPath(
        path.transform(matrix.storage),
        Paint()
          ..color = (selected ? Colors.teal : Colors.grey)
          ..strokeWidth = (selected ? 3 : 1)
          ..style = PaintingStyle.stroke,
      );
      if ((kind == 'order' || kind == 'release') && selected) {
        label(
          canvas,
          view.screen(path.getBounds().center),
          '${ids.indexOf(id) + 1}',
          Colors.deepOrange,
        );
      }
    }
    if (kind == 'tabs') {
      final path = Path();
      if (shape['circle'] != null) {
        final c = shape['circle'] as Json;
        path.addOval(
          Rect.fromCircle(
            center: point(c['center']),
            radius: number(c['radius']),
          ),
        );
      } else {
        final pts = (shape['path'] as List? ?? []).map(point).toList();
        if (pts.isNotEmpty) path.addPolygon(pts, true);
      }
      canvas.drawPath(
        path.transform(matrix.storage),
        Paint()
          ..color = Colors.teal
          ..strokeWidth = 3
          ..style = PaintingStyle.stroke,
      );
      for (var i = 0; i < tabs.length; i++) {
        label(canvas, view.screen(tabs[i]), '${i + 1}', Colors.deepOrange);
      }
    }
    if (kind == 'start') {
      for (var i = 0; i < starts.length; i++) {
        label(
          canvas,
          view.screen(starts[i]),
          '${i + 1}',
          i == index ? Colors.deepOrange : Colors.teal,
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant ManufacturingPickPainter oldDelegate) => true;
}

Widget graphicalReview(Json details, String units) {
  final before = details['before'] as Json, after = details['after'] as Json;
  return Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        pickingNames[details['kind']] ?? 'Graphical changes',
        style: const TextStyle(fontWeight: FontWeight.bold),
      ),
      if (details['kind'] == 'start')
        Text(
          'Preferred start ${number(before['start_index']).toInt() + 1} → ${number(after['start_index']).toInt() + 1}. ${details['manual_tabs_preserved'] == true ? 'Manual tab positions retained.' : 'Automatic tab placement may shift; review holding.'}',
        ),
      if (details['kind'] == 'tabs')
        Text(
          'Manual centers: ${(after['manual_tab_positions'] as List).map((v) => (number(v) * 100).toStringAsFixed(2)).join(', ')}%. Finished width ${after['tab_width']} $units; retained height ${after['tab_height']} $units.',
        ),
      if (details['kind'] == 'boundaries')
        Text(
          'Outer boundaries: ${(before['entity_ids'] as List).length} → ${(after['entity_ids'] as List).length}',
        ),
      if (details['kind'] == 'islands')
        Text(
          'Protected islands: ${(before['island_entity_ids'] as List).length} → ${(after['island_entity_ids'] as List).length}',
        ),
      if (details['kind'] == 'order')
        Text(
          (after['manual_contour_order'] as List).isEmpty
              ? 'Automatic nested-first order'
              : 'Manual order: ${(after['manual_contour_order'] as List).length} contours',
        ),
      const Text(
        'Review the regenerated motion before Apply. The original drawing and selection are unchanged.',
      ),
    ],
  );
}
