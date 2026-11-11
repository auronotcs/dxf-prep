import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/shop_pdf_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';

class PdfModel extends DesignController {
  final commands = <String>[];
  Json? params;
  bool reject = false;
  PdfModel() {
    ready = true;
    busy = false;
    state = {
      'revision': 1,
      'units': 'in',
      'activePlate': 1,
      'capabilities': ['shop_drawing_pdf'],
      'plates': [
        {'number': 1, 'width': 96, 'height': 48, 'x': 0, 'y': 0},
        {'number': 2, 'width': 24, 'height': 12, 'x': 96, 'y': 0},
      ],
      'entities': <Json>[],
      'selected': <String>[],
    };
    preferences = {'grid': true};
  }
  @override
  Future<Json> request(String command, [Json p = const {}]) async {
    commands.add(command);
    params = p;
    if (reject) {
      return {'ok': false, 'error': 'The drawing changed. Prepare again.'};
    }
    if (command == 'design.shop_pdf_export') {
      return {
        'ok': true,
        'output': {'path': p['path']},
      };
    }
    return {
      'ok': true,
      'preview': {
        'token': 'prepared',
        'settings': p,
        'omittedCount': 1,
        'crossingCount': 1,
        'unresolvedCount': 1,
        'pages': [
          {
            'label': 'Plate 1',
            'page_width_in': 17,
            'page_height_in': 11,
            'scale_denominator': 6,
          },
        ],
      },
    };
  }

  @override
  Future<void> savePreferences(Json p) async {
    preferences = p;
  }
}

void main() {
  const channel = MethodChannel('workbench/design');
  Future<void> open(WidgetTester tester, PdfModel m) async {
    tester.view.physicalSize = const Size(1266, 714);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => ShopPdfDialog(model: m),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'PDF prepares active plate, shows omissions, picker cancellation writes nothing',
    (tester) async {
      final m = PdfModel();
      final platformCalls = <String>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(channel, (
        call,
      ) async {
        platformCalls.add(call.method);
        return null;
      });
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          channel,
          null,
        ),
      );
      await open(tester, m);
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Save PDF…'),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.text('Prepare PDF'));
      await tester.pumpAndSettle();
      expect(m.params!['plate_numbers'], [1]);
      expect(find.textContaining('Omitted: 1'), findsOneWidget);
      expect(find.textContaining('unresolved dimension'), findsOneWidget);
      await tester.tap(find.text('Save PDF…'));
      await tester.pumpAndSettle();
      expect(platformCalls, ['pdf']);
      expect(m.commands, ['design.shop_pdf_prepare']);
      await tester.tap(find.text('Plate 2 · 24 × 12 in'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Save PDF…'),
            )
            .onPressed,
        isNull,
      );
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'PDF saves reviewed token, retains preferences and reports viewer failure honestly',
    (tester) async {
      final m = PdfModel();
      final platformCalls = <String>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(channel, (
        call,
      ) async {
        platformCalls.add(call.method);
        if (call.method == 'openPdf') {
          throw PlatformException(code: 'viewer', message: 'No viewer');
        }
        return 'C:/evaluation/shop.pdf';
      });
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          channel,
          null,
        ),
      );
      await open(tester, m);
      await tester.tap(find.text('Prepare PDF'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save PDF…'));
      await tester.pumpAndSettle();
      expect(m.commands, ['design.shop_pdf_prepare', 'design.shop_pdf_export']);
      expect(m.params!['token'], 'prepared');
      expect(m.preferences['grid'], true);
      expect(m.preferences['shopDrawing']['page_size'], 'Tabloid');
      expect(platformCalls, ['pdf', 'openPdf']);
      expect(find.textContaining('PDF saved, but the viewer'), findsOneWidget);
      expect(find.text('Done'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Custom scale validates and stale export requires preparation again',
    (tester) async {
      final m = PdfModel();
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        channel,
        (_) async => 'C:/evaluation/shop.pdf',
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          channel,
          null,
        ),
      );
      await open(tester, m);
      await tester.tap(find.text('Fit to page'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Custom (1:N)').last);
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '0');
      await tester.tap(find.text('Prepare PDF'));
      await tester.pumpAndSettle();
      expect(m.commands, isEmpty);
      await tester.enterText(find.byType(TextField), '8');
      await tester.tap(find.text('Prepare PDF'));
      await tester.pumpAndSettle();
      expect(m.params!['scale_denominator'], 8);
      m.reject = true;
      await tester.tap(find.text('Save PDF…'));
      await tester.pumpAndSettle();
      expect(find.textContaining('The drawing changed'), findsOneWidget);
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Save PDF…'),
            )
            .onPressed,
        isNull,
      );
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets('Ctrl+P opens Shop Drawing without exporting', (tester) async {
    final m = PdfModel();
    await tester.pumpWidget(DesignApp(controller: m, startBackend: false));
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyP);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pumpAndSettle();
    expect(find.byType(ShopPdfDialog), findsOneWidget);
    expect(m.commands, isEmpty);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
