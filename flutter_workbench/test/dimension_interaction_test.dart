import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/dimension_painter.dart';
import 'dimension_test.dart' show DimensionModel, ref;
import 'package:flutter/services.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

Json linear(String kind, {double offset = 5}) {
  final axis = kind == 'horizontal'
      ? const Offset(0, 1)
      : kind == 'vertical'
      ? const Offset(1, 0)
      : const Offset(-10, 20) / const Offset(20, 10).distance;
  Offset a, b;
  if (kind == 'horizontal') {
    a = Offset(0, 5 + offset);
    b = Offset(20, 5 + offset);
  } else if (kind == 'vertical') {
    a = Offset(10 + offset, 0);
    b = Offset(10 + offset, 10);
  } else {
    a = axis * offset;
    b = const Offset(20, 10) + axis * offset;
  }
  Json xy(Offset p) => {'x': p.dx, 'y': p.dy};
  return {
    'id': 'd',
    'annotation': {
      'id': 'd',
      'kind': kind,
      'offset': offset,
      'start': ref(0, 0),
      'end': ref(20, 10),
      'precision': 3,
      'text_height': 1.0,
      'extension_offset': .5,
      'layer': 'DIMENSIONS',
      'metadata': <String, dynamic>{'custom': 'retained'},
      'vertex': null,
    },
    'start': xy(Offset.zero),
    'end': xy(const Offset(20, 10)),
    'dimension_start': xy(a),
    'dimension_end': xy(b),
    'text_point': xy((a + b) / 2),
    'text_rotation': kind == 'vertical'
        ? 90
        : kind == 'aligned'
        ? 26.565
        : 0,
    'extension_lines': [
      [xy(Offset.zero), xy(a)],
      [xy(const Offset(20, 10)), xy(b)],
    ],
    'path': [],
    'arrow_mode': 'both',
    'label': '20.000 in',
    'resolved': true,
  };
}

class DirectDimensionModel extends DimensionModel {
  Json? draft;
  Completer<void>? gate;
  bool failPreview = false;
  DirectDimensionModel() {
    state['dimensionAuthoring'] = true;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command != 'design.dimension_preview') {
      return super.request(command, params);
    }
    calls.add(command);
    draft = params['annotation'] as Json;
    if (gate != null) await gate!.future;
    return failPreview
        ? {'ok': false, 'error': 'Rejected preview'}
        : {
            'ok': true,
            'preview': {
              'token': 'token',
              'dimension': linear(
                draft!['kind'],
                offset: number(draft!['offset']),
              ),
            },
          };
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    await super.run(command, params);
    if (command == 'design.dimension_apply') {
      state['dimensions'] = [
        linear(draft!['kind'], offset: number(draft!['offset'])),
      ];
      notifyListeners();
    }
    return true;
  }
}

void main() {
  testWidgets(
    'Delete shortcut targets selected annotation, never selected geometry',
    (tester) async {
      tester.view.physicalSize = const Size(1440, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final m = TestDesignController();
      addTearDown(m.dispose);
      m.state['dimensionAuthoring'] = true;
      m.state['dimensions'] = [linear('horizontal')];
      m.state['selected'] = ['keep-geometry'];
      await tester.pumpWidget(DesignApp(controller: m, startBackend: false));
      await tester.pumpAndSettle();
      final canvas = tester.widget<DesignCanvas>(find.byType(DesignCanvas));
      final v = canvas.view;
      v.scale = 10;
      v.translation = const Offset(100, 350);
      v.changed();
      await tester.pump();
      await tester.tapAt(
        tester.getTopLeft(find.byType(DesignCanvas)) +
            v.screen(point(m.state['dimensions'][0]['text_point'])),
      );
      await tester.pumpAndSettle();
      expect(v.selectedDimensionId, 'd');
      m.commands.clear();
      await tester.sendKeyEvent(LogicalKeyboardKey.delete);
      await tester.pumpAndSettle();
      expect(find.text('Delete this dimension?'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(m.commands, ['design.dimension_delete']);
      expect(m.selection, {'keep-geometry'});
      expect(tester.takeException(), isNull);
    },
  );

  Future<void> mount(
    WidgetTester tester,
    DirectDimensionModel m,
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
    ..translation = const Offset(100, 350);

  testWidgets(
    'Linear labels drag perpendicular with local preview, stable refs and a single Apply',
    (tester) async {
      final m = DirectDimensionModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final kind in ['horizontal', 'vertical', 'aligned']) {
        final d = linear(kind), axis = dimensionDragAxis(linear(kind))!;
        m.state['dimensions'] = [d];
        m.calls.clear();
        await mount(tester, m, v);
        final from = v.screen(point(d['text_point'])) + const Offset(1, 1);
        final to = from + Offset(axis.dx * 30, -axis.dy * 30);
        final g = await tester.startGesture(from);
        await g.moveTo(to);
        await tester.pump();
        expect(v.selectedDimensionId, 'd');
        expect(v.dimensionPreview!['annotation']['offset'], closeTo(8, 1e-8));
        expect(m.state['dimensions'][0]['annotation']['offset'], 5);
        expect(m.calls, isEmpty);
        await g.up();
        await tester.pumpAndSettle();
        expect(m.calls, ['design.dimension_preview', 'design.dimension_apply']);
        expect(m.draft!['start'], d['annotation']['start']);
        expect(m.draft!['end'], d['annotation']['end']);
        expect(m.draft!['metadata'], {'custom': 'retained'});
        expect(m.selection, {'unchanged'});
        expect(v.dimensionPreview, isNull);
      }
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Dimension hit testing respects rotated text, actual strokes and zoom',
    (tester) async {
      for (final kind in ['horizontal', 'vertical', 'aligned']) {
        final d = linear(kind);
        for (final scale in [1.0, 10.0, 100.0]) {
          expect(hitDimension(d, point(d['text_point']), scale), true);
          expect(hitDimension(d, point(d['dimension_start']), scale), true);
          expect(hitDimension(d, const Offset(90, 90), scale), false);
        }
      }
      expect(hitDimension(linear('horizontal'), const Offset(6, 3), 10), false);
      final d = linear('vertical');
      expect(
        hitDimension(d, point(d['text_point']) + const Offset(0, 2), 10),
        true,
      );
      expect(
        hitDimension(d, point(d['text_point']) + const Offset(2, 0), 10),
        false,
      );
    },
  );

  testWidgets(
    'Locked, hidden and readonly annotations cannot be moved; unsupported kinds stay editable by menu',
    (tester) async {
      final m = DirectDimensionModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final mode in [
        'locked',
        'hidden',
        'annotationHidden',
        'readonly',
        'unsupported',
      ]) {
        final d = linear('horizontal');
        if (mode == 'unsupported') d['annotation']['kind'] = 'diameter';
        if (mode == 'annotationHidden') {
          d['annotation']['metadata']['annotation_hidden'] = true;
        }
        m.state['dimensions'] = [d];
        m.state['layers'] = [
          {
            'name': 'DIMENSIONS',
            'locked': mode == 'locked',
            'visible': mode != 'hidden',
          },
        ];
        m.state['readOnly'] = mode == 'readonly';
        m.calls.clear();
        await mount(tester, m, v);
        final from = v.screen(point(d['text_point']));
        final g = await tester.startGesture(from);
        await g.moveTo(from + const Offset(0, -30));
        await g.up();
        await tester.pumpAndSettle();
        expect(
          m.calls.where((c) => c.startsWith('design.dimension')),
          isEmpty,
          reason: mode,
        );
        expect(m.state['dimensions'][0]['annotation']['offset'], 5);
      }
    },
  );

  testWidgets(
    'Dimension no-op, outside, Escape, cancellation, zoom and stale gestures do not apply',
    (tester) async {
      final m = DirectDimensionModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final mode in [
        'click',
        'tangent',
        'outside',
        'escape',
        'cancel',
        'zoom',
        'stale',
      ]) {
        v.scale = 10;
        v.translation = const Offset(100, 350);
        final d = linear('horizontal');
        m.state['dimensions'] = [d];
        m.calls.clear();
        await mount(tester, m, v);
        final from = v.screen(point(d['text_point']));
        final g = await tester.startGesture(from);
        if (mode != 'click') {
          await g.moveTo(
            from +
                (mode == 'tangent'
                    ? const Offset(30, 0)
                    : const Offset(0, -30)),
          );
        }
        if (mode == 'escape') v.setTool('select');
        if (mode == 'stale') m.state['revision']++;
        if (mode == 'zoom') v.zoom(2, from);
        if (mode == 'outside') await g.moveTo(const Offset(-10, 0));
        if (mode == 'cancel') {
          await g.cancel();
        } else {
          await g.up();
        }
        await tester.pumpAndSettle();
        expect(m.calls, isEmpty, reason: mode);
        expect(v.dimensionPreview, isNull);
      }
    },
  );

  testWidgets(
    'Context Edit uses the existing editor and Delete requires confirmation',
    (tester) async {
      final m = DirectDimensionModel(), v = viewport();
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['dimensions'] = [linear('horizontal')];
      await mount(tester, m, v);
      Future<void> menu() async {
        final g = await tester.startGesture(
          v.screen(point(m.state['dimensions'][0]['text_point'])),
          buttons: kSecondaryMouseButton,
        );
        await g.up();
        await tester.pumpAndSettle();
      }

      await menu();
      await tester.tap(find.text('Edit dimension…'));
      await tester.pumpAndSettle();
      expect(find.text('Line offset / arc radius (mm)'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      await menu();
      await tester.tap(find.text('Delete dimension…'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.state['dimensions'], hasLength(1));
      await menu();
      await tester.tap(find.text('Delete dimension…'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
      await tester.pumpAndSettle();
      expect(m.state['dimensions'], isEmpty);
      expect(m.selection, {'unchanged'});
      expect(
        m.calls.where((c) => c == 'design.dimension_delete'),
        hasLength(1),
      );
      expect(tester.takeException(), isNull);
    },
  );

  test(
    'Concurrent, stale and failed dimension previews never Apply and release busy',
    () async {
      final m = DirectDimensionModel();
      addTearDown(m.dispose);
      final annotation = linear('horizontal')['annotation'] as Json;
      m.gate = Completer<void>();
      final first = m.editDimension(1, annotation);
      expect(m.busy, true);
      expect(await m.editDimension(1, annotation), false);
      m.state['revision'] = 2;
      m.gate!.complete();
      expect(await first, false);
      expect(m.busy, false);
      expect(m.calls, ['design.dimension_preview']);
      expect(await m.editDimension(1, annotation), false);
      m.gate = null;
      m.failPreview = true;
      expect(await m.editDimension(2, annotation), false);
      expect(m.busy, false);
      expect(m.calls, everyElement('design.dimension_preview'));
    },
  );

  test(
    'Real backend linear drag matches preview, one-step undo, stable references, inch/mm and save/reopen',
    () async {
      final m = DesignController(),
          tmp = Directory.systemTemp.createTempSync('dimension-direct-');
      try {
        await m.start();
        expect(m.ready, true, reason: m.message);
        for (final units in ['in', 'mm']) {
          await m.run('project.new', {'units': units});
          await m.run('geometry.add_rectangle', {
            'x': 0,
            'y': 0,
            'width': 20,
            'height': 10,
          });
          final geometry = jsonEncode(m.entities),
              selection = Set.of(m.selection),
              entityId = m.entities.first['id'];
          for (final kind in ['horizontal', 'vertical', 'aligned']) {
            final a = linear(kind)['annotation'] as Json;
            a.remove('id');
            a['start'] = {
              ...ref(0, 0),
              'entity_id': entityId,
              'feature': 'rectangle:corner:0',
            };
            a['end'] = {
              ...ref(20, 10),
              'entity_id': entityId,
              'feature': 'rectangle:corner:2',
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
            final d = (m.state['dimensions'] as List).last as Json,
                old = jsonEncode(d['annotation']);
            final preview = previewDimensionOffset(d, -2.25),
                history = m.state['history']['index'];
            expect(
              await m.editDimension(
                m.state['revision'],
                preview['annotation'] as Json,
              ),
              true,
              reason: m.message,
            );
            final updated = (m.state['dimensions'] as List).last as Json;
            void near(dynamic actual, dynamic expected) {
              if (expected is num) {
                expect(actual, closeTo(expected, 1e-10));
              } else if (expected is Map) {
                for (final key in expected.keys) {
                  near(actual[key], expected[key]);
                }
              } else if (expected is List) {
                expect(actual, hasLength(expected.length));
                for (var i = 0; i < expected.length; i++) {
                  near(actual[i], expected[i]);
                }
              }
            }

            near(updated['dimension_start'], preview['dimension_start']);
            near(updated['text_point'], preview['text_point']);
            near(updated['extension_lines'], preview['extension_lines']);
            expect(m.state['history']['index'], history + 1);
            expect(jsonEncode(m.entities), geometry);
            expect(m.selection, selection);
            await m.run('history.undo');
            expect(jsonEncode(m.state['dimensions'].last['annotation']), old);
            await m.run('history.redo');
            final path = '${tmp.path}/$units-$kind.camjob.json';
            await m.run('project.save', {'path': path});
            await m.run('project.open', {'path': path});
            final saved = m.state['dimensions'].last;
            expect(saved['id'], d['id']);
            expect(saved['annotation']['offset'], -2.25);
            expect(saved['annotation']['start']['entity_id'], entityId);
            await m.run('layer.set', {'name': 'DIMENSIONS', 'locked': true});
            expect(
              await m.editDimension(m.state['revision'], {
                ...saved['annotation'] as Json,
                'offset': 4,
              }),
              false,
            );
            expect(
              await m.run('design.dimension_delete', {'id': d['id']}),
              false,
            );
            await m.run('layer.set', {'name': 'DIMENSIONS', 'locked': false});
            // Opening intentionally resets selection; restore for the next kind.
            await m.select(selection);
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
