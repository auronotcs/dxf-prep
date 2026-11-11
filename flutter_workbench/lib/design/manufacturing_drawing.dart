import 'dart:math' as math;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'canvas.dart' show DesignViewport, DesignPainter, entityPath;
import 'controller.dart';
import 'keepout_painter.dart';

/// Geometry selection only: this view never issues geometry mutation commands.
class ManufacturingDrawing extends StatefulWidget {
  const ManufacturingDrawing({
    super.key,
    required this.model,
    this.enabled = true,
    this.view,
    this.header,
    this.panelColor,
  });
  final DesignController model;
  final bool enabled;
  final DesignViewport? view;
  final Widget? header;
  final Color? panelColor;
  @override
  State<ManufacturingDrawing> createState() => _ManufacturingDrawingState();
}

class _ManufacturingDrawingState extends State<ManufacturingDrawing> {
  late final view = widget.view ?? DesignViewport();
  final focus = FocusNode();
  final paths = <String, Path>{};
  bool fitted = false, panMode = false, panning = false, additive = false;
  Offset? down, last;
  int? pointer;
  dynamic gestureRevision;
  DesignController get m => widget.model;
  bool get enabled => widget.enabled && m.ready && !m.busy;
  Iterable<Json> get selectable => m.selectable.where(
    (e) => ['line', 'circle', 'rectangle', 'polyline'].contains(e['kind']),
  );

  @override
  void dispose() {
    focus.dispose();
    if (widget.view == null) view.dispose();
    view.start = view.current = null;
    super.dispose();
  }

  Rect? drawingBounds({bool stock = false}) {
    Rect? result;
    final rectangles = stock
        ? m.plates.map(
            (p) => Rect.fromLTWH(
              number(p['x']),
              number(p['y']),
              number(p['width']),
              number(p['height']),
            ),
          )
        : m.entities.where(m.visible).map((e) => paths[e['id']]!.getBounds());
    for (final r in rectangles) {
      result = result?.expandToInclude(r) ?? r;
    }
    return result;
  }

  void fit({bool stock = false}) {
    view.fit(
      drawingBounds(stock: stock) ??
          drawingBounds(stock: true) ??
          const Rect.fromLTWH(0, 0, 96, 48),
    );
  }

  String? hit(Offset screen) {
    final world = view.world(screen), tolerance = 7 / view.scale;
    for (final e in selectable.toList().reversed) {
      final path = paths[e['id']]!;
      if (!path.getBounds().inflate(tolerance).contains(world)) continue;
      if ((e['kind'] != 'line' &&
              (e['kind'] != 'polyline' || e['closed'] == true)) &&
          path.contains(world)) {
        return e['id'] as String;
      }
      for (final metric in path.computeMetrics()) {
        final steps = math.min(
          20000,
          math.max(2, (metric.length / tolerance).ceil()),
        );
        for (var i = 0; i <= steps; i++) {
          final tangent = metric.getTangentForOffset(metric.length * i / steps);
          if (tangent != null &&
              (tangent.position - world).distance <= tolerance) {
            return e['id'] as String;
          }
        }
      }
    }
    return null;
  }

  void cancel() {
    pointer = null;
    down = last = null;
    panning = false;
    view.start = view.current = null;
    view.changed();
  }

  void pointerDown(PointerDownEvent event) {
    if (!enabled ||
        pointer != null ||
        event.buttons & (kPrimaryMouseButton | kMiddleMouseButton) == 0) {
      return;
    }
    focus.requestFocus();
    pointer = event.pointer;
    down = last = event.localPosition;
    gestureRevision = m.state['revision'];
    additive =
        HardwareKeyboard.instance.isShiftPressed ||
        HardwareKeyboard.instance.isControlPressed;
    panning = panMode || event.buttons & kMiddleMouseButton != 0;
  }

  void pointerMove(PointerMoveEvent event) {
    if (event.pointer != pointer || down == null) return;
    if (!enabled || m.state['revision'] != gestureRevision) {
      cancel();
      return;
    }
    if (panning) {
      view.translation += event.localPosition - last!;
    } else if ((event.localPosition - down!).distance > 4) {
      view.start = view.world(down!);
      view.current = view.world(event.localPosition);
    }
    last = event.localPosition;
    view.changed();
  }

  Future<void> pointerUp(PointerUpEvent event) async {
    if (event.pointer != pointer || down == null) return;
    if (!enabled || m.state['revision'] != gestureRevision || panning) {
      cancel();
      return;
    }
    final ids = additive ? {...m.selection} : <String>{};
    if ((event.localPosition - down!).distance > 4) {
      final rect = Rect.fromPoints(
        view.world(down!),
        view.world(event.localPosition),
      ).inflate(1e-9);
      for (final e in selectable) {
        final b = paths[e['id']]!.getBounds();
        if (rect.contains(b.topLeft) && rect.contains(b.bottomRight)) {
          ids.add(e['id'] as String);
        }
      }
    } else {
      final id = hit(event.localPosition);
      if (id != null && !(additive && ids.remove(id))) ids.add(id);
    }
    cancel();
    if (ids.length != m.selection.length || !ids.containsAll(m.selection)) {
      await m.select(ids);
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: Listenable.merge([m, view]),
    builder: (context, _) {
      paths.clear();
      for (final e in m.entities) {
        paths[e['id']] = entityPath(e);
      }
      return Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            width: 45,
            color: widget.panelColor,
            child: SingleChildScrollView(
              child: Column(
                children: [
                  Tooltip(
                    message: '${m.selection.length} selected',
                    child: const Icon(Icons.drag_indicator, size: 16),
                  ),
                  IconButton(
                    tooltip: 'Select manufacturing contours',
                    isSelected: !panMode,
                    onPressed: () {
                      cancel();
                      setState(() => panMode = false);
                    },
                    icon: const Icon(Icons.near_me_outlined),
                  ),
                  IconButton(
                    tooltip: 'Pan manufacturing drawing',
                    isSelected: panMode,
                    onPressed: () {
                      cancel();
                      setState(() => panMode = true);
                    },
                    icon: const Icon(Icons.pan_tool_outlined),
                  ),
                  IconButton(
                    tooltip: 'Fit manufacturing drawing',
                    onPressed: fit,
                    icon: const Icon(Icons.fit_screen),
                  ),
                  IconButton(
                    tooltip: 'Fit manufacturing stock',
                    onPressed: () => fit(stock: true),
                    icon: const Icon(Icons.crop_free),
                  ),
                  IconButton(
                    tooltip: 'Clear manufacturing selection',
                    onPressed: enabled && m.selection.isNotEmpty
                        ? () => m.select({})
                        : null,
                    icon: const Icon(Icons.deselect),
                  ),
                ],
              ),
            ),
          ),
          Expanded(
            child: Column(
              children: [
                if (widget.header != null) widget.header!,
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      view.size = constraints.biggest;
                      if (!fitted && widget.view == null) {
                        fitted = true;
                        WidgetsBinding.instance.addPostFrameCallback((_) {
                          if (mounted) fit();
                        });
                      }
                      return Focus(
                        focusNode: focus,
                        onKeyEvent: (_, event) {
                          if (event is KeyDownEvent &&
                              event.logicalKey == LogicalKeyboardKey.escape) {
                            cancel();
                            return KeyEventResult.handled;
                          }
                          return KeyEventResult.ignored;
                        },
                        child: MouseRegion(
                          onHover: (e) {
                            view.cursor = view.world(e.localPosition);
                            view.cursorInside = true;
                            final id = hit(e.localPosition);
                            view.hoveredIds = id == null ? [] : [id];
                            view.changed();
                          },
                          onExit: (_) {
                            view.cursorInside = false;
                            view.hoveredIds = [];
                            view.changed();
                          },
                          cursor: panMode
                              ? SystemMouseCursors.grab
                              : SystemMouseCursors.precise,
                          child: Listener(
                            key: const ValueKey('manufacturing-drawing-canvas'),
                            behavior: HitTestBehavior.opaque,
                            onPointerDown: pointerDown,
                            onPointerMove: pointerMove,
                            onPointerUp: pointerUp,
                            onPointerCancel: (_) => cancel(),
                            onPointerSignal: (event) {
                              if (event is PointerScrollEvent &&
                                  pointer == null) {
                                GestureBinding.instance.pointerSignalResolver
                                    .register(event, (_) {
                                      view.zoom(
                                        math.exp(-event.scrollDelta.dy * .0015),
                                        event.localPosition,
                                      );
                                    });
                              }
                            },
                            child: ClipRect(
                              child: CustomPaint(
                                foregroundPainter: KeepoutPainter(
                                  ((m.state['manufacturing']?['keepouts']
                                              as List?) ??
                                          [])
                                      .cast<Json>(),
                                  view.screen,
                                ),
                                painter: DesignPainter(
                                  m,
                                  view,
                                  paths,
                                  Theme.of(context).brightness ==
                                      Brightness.dark,
                                ),
                                child: m.entities.where(m.visible).isEmpty
                                    ? const Center(
                                        child: Text(
                                          'No visible drawing geometry. Create or open a drawing in Design.',
                                        ),
                                      )
                                    : const SizedBox.expand(),
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ],
      );
    },
  );
}
