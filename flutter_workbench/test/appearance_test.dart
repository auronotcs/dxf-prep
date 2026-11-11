import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/appearance_dialog.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/controller.dart';

Json rgb(int r, int g, int b) => {
  'model': 'rgb',
  'channels': [r, g, b],
  'preview_rgb': [r, g, b],
};

class AppearanceModel extends DesignController {
  final calls = <Json>[];
  AppearanceModel() {
    busy = false;
    ready = true;
    state = {
      'revision': 7,
      'selected': ['rect'],
      'layers': <Json>[],
      'plates': <Json>[],
      'entities': <Json>[
        {
          'id': 'rect',
          'kind': 'rectangle',
          'layer': '0',
          'corner_a': {'x': 2, 'y': 2},
          'corner_b': {'x': 12, 'y': 12},
          'bounds': [2, 2, 12, 12],
          'appearance': {
            'fill_enabled': true,
            'stroke_enabled': true,
            'stroke_width': .5,
            'fill': rgb(255, 0, 0),
            'stroke': rgb(0, 0, 0),
          },
        },
      ],
    };
  }
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add({'command': command, ...params});
    return true;
  }
}

void main() {
  test(
    'Untouched authored colors keep exact channels and palette provenance',
    () {
      final source = <String, dynamic>{
        'model': 'cmyk',
        'channels': [11.123456789, 20, 30, 4],
        'preview_rgb': [218, 196, 171],
        'name': 'Test',
        'profile': '',
        'palette_id': 'synthetic',
        'swatch_id': 'one',
      };
      final draft = ColorDraft(source);
      addTearDown(draft.dispose);
      expect(draft.value(), source);
      draft.channels.text = '0, 100, 100, 0';
      expect(draft.value()['preview_rgb'], [255, 0, 0]);
      expect(draft.value().containsKey('palette_id'), isFalse);
    },
  );

  testWidgets(
    'Appearance validates input, preserves captured revision, and restores layer defaults',
    (tester) async {
      tester.view.physicalSize = const Size(1000, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final model = AppearanceModel();
      addTearDown(model.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => showDialog<void>(
                  context: context,
                  builder: (_) => AppearanceDialog(model: model),
                ),
                child: const Text('Style'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Style'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const ValueKey('stroke-width')), '-1');
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(
        find.text('Stroke width must be zero or positive.'),
        findsOneWidget,
      );
      expect(model.calls, isEmpty);
      await tester.enterText(
        find.byKey(const ValueKey('stroke-width')),
        '0.25',
      );
      await tester.enterText(
        find.byKey(const ValueKey('Fill-channels')),
        '0, 255, 0',
      );
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(model.calls.single['expected_revision'], 7);
      expect(model.calls.single['appearance']['fill']['preview_rgb'], [
        0,
        255,
        0,
      ]);
      expect(model.calls.single['appearance']['stroke_width'], .25);
      await tester.tap(find.text('Style'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('stroke-width')),
        'invalid',
      );
      await tester.tap(find.text('Use layer appearance'));
      await tester.pumpAndSettle();
      expect(model.calls.last['inherit_layer'], isTrue);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Canvas renders explicit fills and physical stroke widths in either theme',
    (tester) async {
      final model = AppearanceModel();
      addTearDown(model.dispose);
      model.state['selected'] = <String>[];
      final view = DesignViewport()
        ..scale = 10
        ..translation = const Offset(20, 180)
        ..grid = false;
      addTearDown(view.dispose);
      for (final dark in [false, true]) {
        final recorder = ui.PictureRecorder();
        final canvas = Canvas(recorder);
        DesignPainter(model, view, {
          'rect': entityPath(model.entities.single),
        }, dark).paint(canvas, const Size(200, 200));
        final picture = recorder.endRecording();
        await tester.runAsync(() async {
          final image = await picture.toImage(200, 200);
          final data = (await image.toByteData(
            format: ui.ImageByteFormat.rawRgba,
          ))!;
          List<int> pixel(int x, int y) => [
            for (var c = 0; c < 3; c++) data.getUint8((y * 200 + x) * 4 + c),
          ];
          expect(pixel(80, 100), [255, 0, 0]);
          expect(pixel(41, 100), [0, 0, 0]);
          expect(pixel(44, 100), [255, 0, 0]);
          image.dispose();
        });
        picture.dispose();
      }
    },
  );
}
