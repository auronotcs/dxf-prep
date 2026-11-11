import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/appearance_dialog.dart';
import 'package:workbench_prototype/design/palette_dialog.dart';
import 'package:workbench_prototype/design/controller.dart';

class PaletteModel extends DesignController {
  final calls = <String>[];
  final color = <String, dynamic>{
    'model': 'cmyk',
    'channels': [88.123456789, 22, 0, 1],
    'preview_rgb': [30, 190, 250],
    'name': 'Exact blue',
    'profile': 'Test profile',
    'palette_id': 'test',
    'swatch_id': 'blue',
  };
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    calls.add(command);
    return switch (command) {
      'palette.list' => {
        'ok': true,
        'digest': 'hash',
        'palettes': [
          {
            'id': 'test',
            'name': 'Test colors',
            'manufacturer': 'Example maker',
            'collection': 'Collection',
            'version': '1',
            'source': 'Synthetic',
            'swatches': [
              {
                'id': 'blue',
                'name': 'Exact blue',
                'product_code': 'T-123',
                'color': color,
              },
            ],
          },
        ],
      },
      'palette.choose' => {'ok': true, 'color': color, 'token': 'chosen'},
      'palette.import_preview' => {
        'ok': true,
        'name': 'Replacement colors',
        'manufacturer': 'Example maker',
        'count': 2,
        'token': 'import',
        'replaces': 'Test colors',
      },
      _ => {'ok': true},
    };
  }
}

void main() {
  test(
    'Palette draft retains provenance and drops token after manual edits',
    () {
      final m = PaletteModel();
      final draft = ColorDraft(m.color);
      addTearDown(draft.dispose);
      draft.usePalette({'color': m.color, 'token': 'chosen'});
      expect(draft.value(), m.color);
      expect(draft.activePaletteToken, 'chosen');
      draft.channels.text = '0, 0, 0, 0';
      expect(draft.activePaletteToken, isNull);
      expect(draft.value().containsKey('palette_id'), isFalse);
    },
  );

  testWidgets(
    'Search by code, choose exact snapshot, and cancel replacement import',
    (tester) async {
      tester.view.physicalSize = const Size(780, 650);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final m = PaletteModel();
      Json? chosen;
      const channel = MethodChannel('workbench/design');
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        channel,
        (call) async => 'test.json',
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          channel,
          null,
        ),
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  chosen = await showDialog<Json>(
                    context: context,
                    builder: (_) => PaletteDialog(model: m),
                  );
                },
                child: const Text('Open'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'missing');
      await tester.pump();
      expect(find.text('No matching swatches.'), findsOneWidget);
      await tester.enterText(find.byType(TextField), 't-123');
      await tester.pump();
      expect(find.text('Exact blue T-123'), findsOneWidget);
      await tester.tap(find.text('Import…'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.textContaining('Replace Test colors'), findsOneWidget);
      await tester.tap(find.text('Cancel').last);
      await tester.pumpAndSettle();
      expect(m.calls.contains('palette.import_apply'), isFalse);
      await tester.tap(find.text('Exact blue T-123'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Use swatch'));
      await tester.pumpAndSettle();
      expect(chosen!['color'], m.color);
      expect(chosen!['token'], 'chosen');
      expect(tester.takeException(), isNull);
    },
  );
}
