import 'dart:math' as math;
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/dimension_painter.dart';
import 'dimension_test.dart' show DimensionModel, ref;

Json xy(Offset p) => {'x': p.dx, 'y': p.dy};
Json radial(String kind, {bool reflex = false, double offset = 5}) {
  const vertex = Offset(10, 10), start = Offset(20, 10), end = Offset(10, 20);
  final sweep = reflex ? -math.pi * 1.5 : math.pi / 2;
  final radius = offset.abs(),
      mid = Offset(math.cos(sweep / 2), math.sin(sweep / 2));
  final path = [
    for (var i = 0; i <= 54; i++)
      xy(
        vertex +
            Offset(math.cos(sweep * i / 54), math.sin(sweep * i / 54)) * radius,
      ),
  ];
  final a = <String, dynamic>{
    'id': 'd',
    'kind': kind,
    'start': kind == 'radius' ? ref(10, 10) : ref(20, 10),
    'end': kind == 'radius' ? ref(20, 10) : ref(10, 20),
    'vertex': kind == 'angle' ? ref(10, 10) : null,
    'offset': offset,
    'precision': 3,
    'text_height': 1.0,
    'extension_offset': .5,
    'layer': 'DIMENSIONS',
    'metadata': <String, dynamic>{
      'angle_mode': reflex ? 'reflex' : 'minor',
      'custom': 'preserve',
    },
  };
  return {
    'id': 'd',
    'annotation': a,
    'resolved': true,
    'value': kind == 'radius'
        ? 10
        : reflex
        ? 270
        : 90,
    'start': xy(kind == 'radius' ? vertex : start),
    'end': xy(kind == 'radius' ? start : end),
    'dimension_start': kind == 'radius' ? xy(vertex) : path.first,
    'dimension_end': kind == 'radius' ? xy(start) : path.last,
    'text_point': xy(
      kind == 'radius'
          ? start + Offset(offset, 0)
          : vertex + mid * (radius + .9),
    ),
    'text_rotation': 0,
    'label': kind == 'radius'
        ? 'R 10.000 in'
        : reflex
        ? '270.000°'
        : '90.000°',
    'path': kind == 'radius' ? <Json>[] : path,
    'arrow_mode': kind == 'radius' ? 'last' : 'both',
    'extension_lines': kind == 'radius'
        ? []
        : [
            [
              xy(vertex + const Offset(.5, 0)),
              xy(vertex + Offset(radius + .25, 0)),
            ],
            [
              xy(vertex + const Offset(0, .5)),
              xy(vertex + Offset(0, radius + .25)),
            ],
          ],
  };
}

class RadialModel extends DimensionModel {
  Json? proposed;
  RadialModel() {
    state['dimensionAuthoring'] = true;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command != 'design.dimension_preview') {
      return super.request(command, params);
    }
    calls.add(command);
    payload = params;
    proposed = previewDimensionOffset(
      (state['dimensions'] as List).first as Json,
      number(params['annotation']['offset']),
    );
    return {
      'ok': true,
      'preview': {'token': 'radial', 'dimension': proposed},
    };
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    await super.run(command, params);
    if (command == 'design.dimension_apply') {
      state['dimensions'] = [proposed];
      notifyListeners();
    }
    return true;
  }
}

void near(dynamic actual, dynamic expected) {
  if (expected is num) {
    expect(actual, closeTo(expected, 1e-9));
  } else if (expected is Map) {
    for (final key in expected.keys) {
      near(actual[key], expected[key]);
    }
  } else if (expected is List) {
    expect(actual, hasLength(expected.length));
    for (var i = 0; i < expected.length; i++) {
      near(actual[i], expected[i]);
    }
  } else {
    expect(actual, expected);
  }
}

void main() {
  Future<void> mount(
    WidgetTester tester,
    RadialModel m,
    DesignViewport v,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AnimatedBuilder(
            animation: m,
            builder: (_, _) =>
                DesignCanvas(model: m, view: v, layer: '0', dark: false),
          ),
        ),
      ),
    );
    await tester.pump();
  }

  DesignViewport viewport() => DesignViewport()
    ..scale = 10
    ..translation = const Offset(100, 400);

  testWidgets(
    'Radius diamond and angular round handles drag with local preview and single Apply',
    (tester) async {
      final m = RadialModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final mode in ['radius', 'minor', 'reflex', 'negative']) {
        final d = radial(
          mode == 'radius' ? 'radius' : 'angle',
          reflex: mode == 'reflex',
          offset: mode == 'negative' ? -5 : 5,
        );
        m.state['dimensions'] = [d];
        m.calls.clear();
        await mount(tester, m, v);
        final handle = dimensionHandle(d, v.scale)!,
            axis = dimensionDragAxis(d)!;
        final from = v.screen(handle) + const Offset(1, 1);
        final worldFrom = v.world(from);
        final target = worldFrom + axis * 3;
        final desired = draggedDimensionOffset(d, worldFrom, target)!;
        final g = await tester.startGesture(from);
        await g.moveTo(v.screen(target));
        await tester.pump();
        expect(v.selectedDimensionId, 'd');
        expect(v.dimensionPreview, isNotNull);
        expect(
          v.dimensionPreview!['annotation']['offset'],
          closeTo(desired, 1e-9),
        );
        expect(
          m.state['dimensions'][0]['annotation']['offset'],
          mode == 'negative' ? -5 : 5,
        );
        expect(m.calls, isEmpty);
        expect(v.dimensionPreview!['value'], d['value']);
        expect(v.dimensionPreview!['label'], d['label']);
        if (mode == 'radius') {
          expect(v.dimensionPreview!['dimension_start'], d['dimension_start']);
          expect(v.dimensionPreview!['dimension_end'], d['dimension_end']);
        }
        await g.up();
        await tester.pumpAndSettle();
        expect(m.calls, ['design.dimension_preview', 'design.dimension_apply']);
        final updated = m.state['dimensions'][0]['annotation'];
        for (final key in ['start', 'end', 'vertex', 'metadata']) {
          expect(updated[key], d['annotation'][key]);
        }
        expect(m.selection, {'unchanged'});
      }
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Any arc location drags radially without a jump; zero radius release cancels',
    (tester) async {
      final m = RadialModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      final d = radial('angle', offset: -5);
      m.state['dimensions'] = [d];
      await mount(tester, m, v);
      final arc = point(d['path'][8]),
          vertex = point(d['annotation']['vertex']['fallback']);
      final g = await tester.startGesture(v.screen(arc));
      await g.moveTo(v.screen(vertex + (arc - vertex) * 1.4));
      await tester.pump();
      expect(v.dimensionPreview!['annotation']['offset'], closeTo(-7, 1e-9));
      await g.cancel();
      await tester.pumpAndSettle();
      expect(m.calls, isEmpty);
      final zero = await tester.startGesture(v.screen(arc));
      await zero.moveTo(v.screen(vertex));
      await tester.pump();
      expect(v.dimensionPreview, isNull);
      expect(v.hint, contains('greater than zero'));
      await zero.up();
      await tester.pumpAndSettle();
      expect(m.calls, isEmpty);
      expect(
        draggedDimensionOffset(d, vertex + const Offset(6, 0), vertex),
        isNull,
      );
      expect(
        draggedDimensionOffset(d, arc, const Offset(double.infinity, 0)),
        isNull,
      );
    },
  );

  testWidgets(
    'Radial handles respect locks, visibility, readonly and cancellation',
    (tester) async {
      final m = RadialModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final kind in ['radius', 'angle']) {
        for (final mode in [
          'locked',
          'hidden',
          'readonly',
          'escape',
          'outside',
          'stale',
          'zoom',
          'cancel',
        ]) {
          v.scale = 10;
          v.translation = const Offset(100, 400);
          final d = radial(kind);
          m.state['dimensions'] = [d];
          m.calls.clear();
          m.state['layers'] = [
            {
              'name': 'DIMENSIONS',
              'locked': mode == 'locked',
              'visible': mode != 'hidden',
            },
          ];
          m.state['readOnly'] = mode == 'readonly';
          await mount(tester, m, v);
          final from = v.screen(dimensionHandle(d, v.scale)!);
          final g = await tester.startGesture(from);
          await g.moveTo(from + const Offset(30, -30));
          if (mode == 'escape') v.setTool('select');
          if (mode == 'stale') m.state['revision']++;
          if (mode == 'zoom') v.zoom(2, from);
          if (mode == 'outside') await g.moveTo(const Offset(-20, 0));
          if (mode == 'cancel') {
            await g.cancel();
          } else {
            await g.up();
          }
          await tester.pumpAndSettle();
          expect(
            m.calls.where((c) => c.startsWith('design.dimension')),
            isEmpty,
            reason: '$kind $mode',
          );
          expect(v.dimensionPreview, isNull);
        }
      }
    },
  );

  testWidgets(
    'Handles remain hittable at multiple zooms and no-op radial motion stays unchanged',
    (tester) async {
      for (final kind in ['radius', 'angle']) {
        final d = radial(kind);
        for (final scale in [1.0, 10.0, 100.0]) {
          final h = dimensionHandle(d, scale)!;
          expect(hitDimension(d, h + Offset(7 / scale, 0), scale), true);
        }
      }
      final radius = radial('radius');
      expect(
        draggedDimensionOffset(
          radius,
          const Offset(25, 10),
          const Offset(25, 15),
        ),
        5,
      );
      expect(previewDimensionOffset(radius, -4)['text_point'], {
        'x': 16.0,
        'y': 10.0,
      });
      final angle = radial('angle');
      expect(
        draggedDimensionOffset(
          angle,
          const Offset(15, 10),
          const Offset(10, 15),
        ),
        5,
      );
      expect(() => previewDimensionOffset(angle, 0), throwsArgumentError);
    },
  );

  test(
    'Real Python previews match radial/angle display and preserve refs, units, history and persistence',
    () async {
      final m = DesignController(),
          tmp = Directory.systemTemp.createTempSync('radial-dim-');
      try {
        await m.start();
        expect(m.ready, true, reason: m.message);
        for (final units in ['in', 'mm']) {
          final factor = units == 'mm' ? 25.4 : 1.0;
          for (final mode in [
            'radius',
            'radius-left',
            'minor',
            'reflex',
            'clockwise',
            'negative',
          ]) {
            await m.run('project.new', {'units': units});
            await m.run('geometry.add_circle', {
              'x': 10 * factor,
              'y': 10 * factor,
              'diameter': 20 * factor,
            });
            final id = m.entities.first['id'];
            Json linked(String feature) => {
              'entity_id': id,
              'feature': 'circle:$feature',
              'fallback': {'x': 0, 'y': 0},
            };
            final a = <String, dynamic>{
              'kind': mode.startsWith('radius') ? 'radius' : 'angle',
              'start': linked(mode.startsWith('radius') ? 'center' : 'right'),
              'end': linked(
                mode == 'radius-left'
                    ? 'left'
                    : mode == 'radius'
                    ? 'right'
                    : mode == 'clockwise'
                    ? 'bottom'
                    : 'top',
              ),
              'vertex': mode.startsWith('radius') ? null : linked('center'),
              'offset': (mode == 'negative' ? -5 : 5) * factor,
              'precision': 3,
              'text_height': .5 * factor,
              'extension_offset': .1 * factor,
              'layer': 'DIMENSIONS',
              'metadata': {
                'angle_mode': mode == 'reflex' ? 'reflex' : 'minor',
                'custom': 'retained',
              },
            };
            final r = await m.request('design.dimension_preview', {
              'annotation': a,
            });
            expect(r['ok'], true, reason: '$r');
            expect(
              await m.run('design.dimension_apply', {
                'token': r['preview']['token'],
              }),
              true,
            );
            // Verify angular vertex fallback follows linked geometry after a transform.
            await m.select({id});
            await m.run('geometry.transform', {
              'dx': 3 * factor,
              'dy': -2 * factor,
            });
            final d = (m.state['dimensions'] as List).first as Json;
            final before = jsonEncode(d['annotation']),
                geometry = jsonEncode(m.entities);
            final preview = previewDimensionOffset(
              d,
              (mode == 'negative' ? -8 : 8) * factor,
            );
            final history = m.state['history']['index'];
            expect(
              await m.editDimension(
                m.state['revision'],
                preview['annotation'] as Json,
              ),
              true,
              reason: m.message,
            );
            final actual = (m.state['dimensions'] as List).first as Json;
            for (final field in [
              'path',
              'dimension_start',
              'dimension_end',
              'text_point',
              'extension_lines',
              'label',
              'value',
            ]) {
              near(actual[field], preview[field]);
            }
            for (final field in ['start', 'end', 'vertex', 'metadata']) {
              expect(actual['annotation'][field], d['annotation'][field]);
            }
            expect(m.state['history']['index'], history + 1);
            expect(jsonEncode(m.entities), geometry);
            await m.run('history.undo');
            expect(jsonEncode(m.state['dimensions'][0]['annotation']), before);
            await m.run('history.redo');
            final path = '${tmp.path}/$units-$mode.camjob.json';
            await m.run('project.save', {'path': path});
            await m.run('project.open', {'path': path});
            expect(
              m.state['dimensions'][0]['annotation'],
              actual['annotation'],
            );
            expect(m.state['dimensions'][0]['resolved'], true);
          }
        }
      } finally {
        await m.shutdown();
        m.dispose();
        tmp.deleteSync(recursive: true);
      }
    },
  );
}
