import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/dimension_painter.dart';
import 'dimension_test.dart' show DimensionModel, ref;
import 'radial_dimension_test.dart' show near;

Json landing(String kind, {bool linked = false}) {
  final a = <String, dynamic>{
    'id': 'd',
    'kind': kind,
    'start': ref(10, 10),
    'end': linked
        ? {
            'entity_id': 'circle',
            'feature': 'circle:right',
            'fallback': {'x': 25, 'y': 20},
          }
        : ref(25, 20),
    'offset': 2.0,
    'precision': 3,
    'text_height': 1.0,
    'extension_offset': .5,
    'layer': 'DIMENSIONS',
    'metadata': {'text': 'Check edge', 'custom': 'retained'},
  };
  return {
    'id': 'd',
    'annotation': a,
    'resolved': true,
    'start': {'x': 10, 'y': 10},
    'end': {'x': 25, 'y': 20},
    'dimension_start': kind == 'note' ? {'x': 25, 'y': 20} : {'x': 10, 'y': 10},
    'dimension_end': {'x': 25, 'y': 20},
    'text_point': {'x': 25, 'y': 20},
    'text_rotation': 0,
    'label': 'Check edge',
    'value': kind == 'note' ? 0 : 18.027756377319946,
    'path': <Json>[],
    'extension_lines': <Json>[],
    'arrow_mode': kind == 'note' ? 'none' : 'first',
  };
}

class LandingModel extends DimensionModel {
  Json? proposed;
  LandingModel() {
    state['dimensionAuthoring'] = true;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command != 'design.dimension_preview') {
      return super.request(command, params);
    }
    calls.add(command);
    final d = state['dimensions'][0] as Json;
    proposed = previewDimensionDrag(
      d,
      point(d['end']),
      point(params['annotation']['end']['fallback']),
    );
    return {
      'ok': true,
      'preview': {'token': 'landing', 'dimension': proposed},
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

void main() {
  DesignViewport viewport() => DesignViewport()
    ..scale = 10
    ..translation = const Offset(100, 400);
  Future<void> mount(WidgetTester t, LandingModel m, DesignViewport v) async {
    await t.pumpWidget(
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
    await t.pump();
  }

  testWidgets(
    'Leader/note labels and square handles move freely with one Apply and unchanged anchors',
    (t) async {
      final m = LandingModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final kind in ['leader', 'note']) {
        for (final grab in ['label', 'handle']) {
          final d = landing(kind);
          m.state['dimensions'] = [d];
          m.calls.clear();
          await mount(t, m, v);
          final from =
              v.screen(
                grab == 'handle'
                    ? dimensionHandle(d, v.scale)!
                    : point(d['text_point']),
              ) +
              const Offset(2, 1);
          final g = await t.startGesture(from);
          await g.moveTo(from + const Offset(30, -40));
          await t.pump();
          final preview = v.dimensionPreview!;
          near(preview['text_point'], {'x': 28.0, 'y': 24.0});
          expect(preview['annotation']['start'], d['annotation']['start']);
          expect(
            preview['annotation']['metadata'],
            d['annotation']['metadata'],
          );
          expect(preview['label'], d['label']);
          expect(m.calls, isEmpty);
          await g.up();
          await t.pumpAndSettle();
          expect(m.calls, [
            'design.dimension_preview',
            'design.dimension_apply',
          ]);
          near(m.state['dimensions'][0]['annotation'], preview['annotation']);
          expect(m.selection, {'unchanged'});
        }
      }
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Linked landings cannot detach and explain explicit reference editing',
    (t) async {
      final m = LandingModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final kind in ['leader', 'note']) {
        final d = landing(kind, linked: true);
        m.state['dimensions'] = [d];
        m.calls.clear();
        await mount(t, m, v);
        expect(dimensionHandle(d, 10), isNull);
        expect(
          previewDimensionDrag(d, Offset.zero, const Offset(3, 4)),
          isNull,
        );
        final g = await t.startGesture(v.screen(point(d['text_point'])));
        await g.moveBy(const Offset(30, -40));
        await g.up();
        await t.pumpAndSettle();
        expect(m.calls, isEmpty);
        expect(v.hint, contains('Free coordinates'));
        expect(m.state['dimensions'][0], d);
      }
    },
  );
  testWidgets(
    'Landing guards cover visibility locks readonly cancel outside stale zoom and no-op',
    (t) async {
      final m = LandingModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final kind in ['leader', 'note']) {
        for (final mode in [
          'locked',
          'hidden',
          'readonly',
          'escape',
          'outside',
          'stale',
          'zoom',
          'cancel',
          'noop',
        ]) {
          v.scale = 10;
          v.translation = const Offset(100, 400);
          final d = landing(kind);
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
          await mount(t, m, v);
          final from = v.screen(dimensionHandle(d, v.scale)!);
          final g = await t.startGesture(from);
          await g.moveBy(const Offset(30, -30));
          if (mode == 'escape') v.setTool('select');
          if (mode == 'stale') m.state['revision']++;
          if (mode == 'zoom') v.zoom(2, from);
          if (mode == 'outside') await g.moveTo(const Offset(-20, 0));
          if (mode == 'noop') await g.moveTo(from);
          if (mode == 'cancel') {
            await g.cancel();
          } else {
            await g.up();
          }
          await t.pumpAndSettle();
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
    'Leader collapse cancels; notes allow overlap; handles scale and previews do not mutate',
    (t) async {
      for (final kind in ['leader', 'note']) {
        final d = landing(kind), before = jsonEncode(landing(kind));
        for (final scale in [1.0, 10.0, 100.0]) {
          expect(
            hitDimension(
              d,
              dimensionHandle(d, scale)! + Offset(7 / scale, 0),
              scale,
            ),
            true,
          );
        }
        expect(
          previewDimensionDrag(
            d,
            Offset.zero,
            const Offset(double.infinity, 0),
          ),
          isNull,
        );
        final preview = previewDimensionDrag(
          d,
          point(d['end']),
          point(d['start']),
        );
        if (kind == 'leader') {
          expect(preview, isNull);
        } else {
          expect(preview!['text_point'], d['start']);
        }
        expect(jsonEncode(d), before);
        expect(
          dimensionPlacementChanged(
            d['annotation'],
            previewDimensionDrag(d, Offset.zero, Offset.zero)!['annotation'],
          ),
          false,
        );
      }
      final m = LandingModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      final d = landing('leader');
      m.state['dimensions'] = [d];
      await mount(t, m, v);
      final g = await t.startGesture(v.screen(point(d['text_point'])));
      await g.moveTo(v.screen(point(d['start'])));
      await t.pump();
      expect(v.dimensionPreview, isNull);
      await g.up();
      await t.pumpAndSettle();
      expect(m.calls, isEmpty);
    },
  );
  test(
    'Real Python free landings preserve moved linked anchors, inch/mm, Undo/Redo and save',
    () async {
      final m = DesignController(),
          tmp = Directory.systemTemp.createTempSync('landing-dim-');
      try {
        await m.start();
        expect(m.ready, true, reason: m.message);
        for (final units in ['in', 'mm']) {
          final factor = units == 'mm' ? 25.4 : 1.0;
          for (final kind in ['leader', 'note']) {
            await m.run('project.new', {'units': units});
            await m.run('geometry.add_circle', {
              'x': 10 * factor,
              'y': 10 * factor,
              'diameter': 10 * factor,
            });
            final id = m.entities.first['id'],
                a = landing(kind)['annotation'] as Json;
            a.remove('id');
            a['start'] = {
              'entity_id': id,
              'feature': 'circle:center',
              'fallback': {'x': 0, 'y': 0},
            };
            a['end'] = ref(25 * factor, 20 * factor);
            a['text_height'] = factor;
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
            await m.select({id});
            await m.run('geometry.transform', {
              'dx': 3 * factor,
              'dy': -2 * factor,
            });
            final d = m.state['dimensions'][0] as Json,
                before = jsonEncode(m.state['dimensions'][0]['annotation']);
            final geometry = jsonEncode(m.entities),
                history = m.state['history']['index'];
            final preview = previewDimensionDrag(
              d,
              const Offset(1, 2),
              Offset(1 + 4 * factor, 2 - 3 * factor),
            )!;
            expect(
              await m.editDimension(m.state['revision'], preview['annotation']),
              true,
              reason: m.message,
            );
            final actual = m.state['dimensions'][0] as Json;
            for (final field in [
              'start',
              'end',
              'dimension_start',
              'dimension_end',
              'text_point',
              'path',
              'extension_lines',
              'label',
              'value',
              'text_rotation',
              'arrow_mode',
            ]) {
              near(actual[field], preview[field]);
            }
            for (final field in [
              'start',
              'metadata',
              'offset',
              'precision',
              'text_height',
              'extension_offset',
              'layer',
            ]) {
              expect(actual['annotation'][field], d['annotation'][field]);
            }
            expect(m.state['history']['index'], history + 1);
            expect(jsonEncode(m.entities), geometry);
            await m.run('history.undo');
            expect(jsonEncode(m.state['dimensions'][0]['annotation']), before);
            await m.run('history.redo');
            final path = '${tmp.path}/$units-$kind.camjob.json';
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
