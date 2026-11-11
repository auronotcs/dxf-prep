import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'canvas.dart';
import 'controller.dart';

double entityLength(Json e) {
  switch (e['kind']) {
    case 'line':
      return (point(e['end']) - point(e['start'])).distance;
    case 'circle':
      return 2 * math.pi * number(e['radius']);
    case 'rectangle':
      final delta = point(e['corner_b']) - point(e['corner_a']);
      return 2 * (delta.dx.abs() + delta.dy.abs());
    case 'polyline':
      final points = (e['points'] as List).map(point).toList();
      var length = 0.0;
      for (var i = 1; i < points.length; i++) {
        length += (points[i] - points[i - 1]).distance;
      }
      if (e['closed'] == true && points.length > 1) {
        length += (points.last - points.first).distance;
      }
      return length;
    default:
      return 0;
  }
}

String measurementText(DesignController model) {
  final selected = model.entities
      .where((e) => model.selection.contains(e['id']) && model.visible(e))
      .toList();
  if (selected.isEmpty) return 'Perimeter --';
  final reported = model.state['selectionMeasurement'] as Json?;
  String caption;
  if (selected.length > 1) {
    caption = 'Total length (${selected.length})';
  } else {
    final e = selected.single;
    caption = e['kind'] == 'text'
        ? 'Editable annotation'
        : ['circle', 'rectangle'].contains(e['kind']) ||
              e['kind'] == 'polyline' && e['closed'] == true
        ? 'Perimeter'
        : 'Length';
  }
  caption = reported?['caption'] as String? ?? caption;
  if (caption == 'Editable annotation') return caption;
  final length = reported?['value'] is num
      ? number(reported!['value'])
      : selected.fold<double>(0, (sum, e) => sum + entityLength(e));
  return '$caption ${length.toStringAsFixed(4)} ${model.units}';
}

String hoverText(DesignController model, DesignViewport view) {
  final hits = view.hoveredIds
      .map(
        (id) => model.entities
            .where((e) => e['id'] == id && model.visible(e))
            .firstOrNull,
      )
      .whereType<Json>()
      .toList();
  if (!view.cursorInside || hits.isEmpty) return 'Object under pointer: --';
  final e = hits.first;
  final meta = e['metadata'] as Json? ?? {};
  final id = e['id'] as String;
  final number = meta['object_number'];
  final name = (meta['name'] as String? ?? '').trim();
  final identity = number != null
      ? 'Object $number'
      : 'ID …${id.substring(math.max(0, id.length - 8))}';
  return '${name.isEmpty ? identity : '$name ($identity)'} · ${e['kind']} · ${e['layer']}${hits.length > 1 ? ' · +${hits.length - 1} stacked' : ''}';
}

class DesignHistoryBar extends StatelessWidget {
  const DesignHistoryBar({
    super.key,
    required this.model,
    required this.view,
    required this.color,
    required this.undo,
    required this.redo,
  });
  final DesignController model;
  final DesignViewport view;
  final Color color;
  final VoidCallback undo, redo;

  Future<void> go(int index) async {
    view.setTool('select');
    await model.run('history.goto', {'index': index});
  }

  @override
  Widget build(BuildContext context) {
    final history = model.state['history'] as Json?;
    final entries = (history?['entries'] as List? ?? []).cast<String>();
    final current = history?['index'] as int? ?? 0;
    final enabled =
        model.ready &&
        !model.busy &&
        (!model.readOnly ||
            model.state['manufacturing']?['editable'] == true ||
            model.state['cabinetAuthoring'] == true);
    final restart = history == null
        ? ' — save and restart development mode to enable'
        : '';
    return Container(
      key: const ValueKey('history-bar'),
      height: 34,
      color: color,
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          const Text(
            'HISTORY',
            style: TextStyle(fontSize: 10, fontWeight: FontWeight.w700),
          ),
          const SizedBox(width: 6),
          IconButton(
            tooltip: 'First history state$restart',
            onPressed: enabled && current > 0 ? () => go(0) : null,
            icon: const Icon(Icons.first_page, size: 19),
          ),
          IconButton(
            tooltip: 'Undo',
            onPressed: enabled && model.state['canUndo'] == true ? undo : null,
            icon: const Icon(Icons.arrow_left, size: 22),
          ),
          IconButton(
            tooltip: 'Redo',
            onPressed: enabled && model.state['canRedo'] == true ? redo : null,
            icon: const Icon(Icons.arrow_right, size: 22),
          ),
          IconButton(
            tooltip: 'Latest history state$restart',
            onPressed: enabled && current < entries.length - 1
                ? () => go(entries.length - 1)
                : null,
            icon: const Icon(Icons.last_page, size: 19),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: entries.isEmpty
                ? const Text(
                    'History timeline available after save/restart',
                    style: TextStyle(fontSize: 10),
                  )
                : ListView.builder(
                    scrollDirection: Axis.horizontal,
                    itemCount: entries.length,
                    itemExtent: 34,
                    itemBuilder: (context, index) => IconButton(
                      key: ValueKey('history-entry-$index'),
                      tooltip: '$index · ${entries[index]}',
                      isSelected: index == current,
                      style: IconButton.styleFrom(
                        backgroundColor: index == current
                            ? Theme.of(context).colorScheme.primaryContainer
                            : Colors.transparent,
                      ),
                      onPressed: enabled && index != current
                          ? () => go(index)
                          : null,
                      icon: Icon(
                        index == 0
                            ? Icons.diamond_outlined
                            : Icons.circle_outlined,
                        size: 14,
                      ),
                      selectedIcon: Icon(
                        index == 0 ? Icons.diamond : Icons.circle,
                        size: 14,
                      ),
                    ),
                  ),
          ),
          if (MediaQuery.sizeOf(context).width >= 1100)
            SizedBox(
              width: 300,
              child: AnimatedBuilder(
                animation: view,
                builder: (context, _) => Tooltip(
                  message: view.hint,
                  child: Text(
                    view.hint,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 10),
                  ),
                ),
              ),
            ),
          IconButton(
            tooltip: 'Fit drawing',
            onPressed: model.ready
                ? () => view.fit(
                    bounds(model.state['bounds']) ??
                        const Rect.fromLTWH(0, 0, 96, 48),
                  )
                : null,
            icon: const Icon(Icons.fit_screen, size: 18),
          ),
          IconButton(
            tooltip: 'Zoom out',
            onPressed: () => view.zoom(.8, view.size.center(Offset.zero)),
            icon: const Icon(Icons.remove, size: 16),
          ),
          IconButton(
            tooltip: 'Zoom in',
            onPressed: () => view.zoom(1.25, view.size.center(Offset.zero)),
            icon: const Icon(Icons.add, size: 16),
          ),
        ],
      ),
    );
  }
}

class DesignStatusBar extends StatelessWidget {
  const DesignStatusBar({
    super.key,
    required this.model,
    required this.view,
    required this.color,
  });
  final DesignController model;
  final DesignViewport view;
  final Color color;

  Widget field(String text, {double? width, String? tip, Key? fieldKey}) =>
      SizedBox(
        width: width,
        child: Tooltip(
          message: tip ?? text,
          child: Text(
            text,
            key: fieldKey,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(fontSize: 11),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: view,
    builder: (context, _) {
      final b = bounds(model.state['selectionBounds']);
      final coords = view.cursorInside
          ? 'X ${view.cursor.dx.toStringAsFixed(4)}   Y ${view.cursor.dy.toStringAsFixed(4)} ${model.units}'
          : 'X --   Y --';
      final size = b == null
          ? 'Size X --   Y --'
          : 'Size X ${b.width.toStringAsFixed(4)}   Y ${b.height.toStringAsFixed(4)} ${model.units}';
      final zoom = view.zoomPercent(model.units);
      final zoomButton = Tooltip(
        message:
            'View magnification; click for 100%. 100% uses 96 logical pixels per inch with Windows display scaling. Geometry dimensions stay unchanged.',
        child: TextButton(
          onPressed: () => view.zoom(100 / zoom, view.size.center(Offset.zero)),
          style: TextButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 6),
            minimumSize: const Size(96, 24),
          ),
          child: Text(
            'Zoom ${zoom.toStringAsFixed(zoom < 10 ? 2 : 1)}%',
            key: const ValueKey('zoom-percentage'),
            style: const TextStyle(fontSize: 11),
          ),
        ),
      );
      final message = Row(
        children: [
          if (model.busy)
            const SizedBox(
              width: 10,
              height: 10,
              child: CircularProgressIndicator(strokeWidth: 1.5),
            )
          else
            Icon(
              Icons.circle,
              size: 6,
              color: model.ready ? Colors.teal : Colors.red,
            ),
          const SizedBox(width: 6),
          Expanded(child: field(model.message)),
        ],
      );
      return Container(
        key: const ValueKey('drawing-status-bar'),
        color: color,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: LayoutBuilder(
          builder: (context, c) {
            final measurements = [
              field(
                coords,
                width: 205,
                fieldKey: const ValueKey('cursor-readout'),
              ),
              field(size, width: 240, fieldKey: const ValueKey('size-readout')),
              field(
                measurementText(model),
                width: 210,
                fieldKey: const ValueKey('length-readout'),
              ),
            ];
            if (c.maxWidth >= 1250) {
              return SizedBox(
                height: 26,
                child: Row(
                  children: [
                    ...measurements,
                    Expanded(child: message),
                    zoomButton,
                    field(
                      hoverText(model, view),
                      width: 285,
                      fieldKey: const ValueKey('hover-readout'),
                    ),
                  ],
                ),
              );
            }
            return Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SizedBox(
                  height: 24,
                  child: SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(children: [...measurements, zoomButton]),
                  ),
                ),
                SizedBox(
                  height: 24,
                  child: Row(
                    children: [
                      Expanded(child: message),
                      const SizedBox(width: 8),
                      Expanded(
                        child: field(
                          hoverText(model, view),
                          fieldKey: const ValueKey('hover-readout'),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            );
          },
        ),
      );
    },
  );
}
