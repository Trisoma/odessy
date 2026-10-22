import 'package:flutter/material.dart';
import 'app/app.dart';
import 'app/demo/scenario_api.dart';

void main() => runApp(const _Gallery());

class _Gallery extends StatefulWidget {
  const _Gallery();
  @override
  State<_Gallery> createState() => _GalleryState();
}

class _GalleryState extends State<_Gallery> {
  DemoScenario scenario = DemoScenario.empty;
  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        home: Scaffold(
            body: Column(children: [
          Material(
              color: const Color(0xffe2e8e5),
              child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
                  child: Row(children: [
                    const Text('DEVELOPMENT GALLERY · fixtures only'),
                    const SizedBox(width: 24),
                    DropdownButton<DemoScenario>(
                        value: scenario,
                        items: DemoScenario.values
                            .map((s) =>
                                DropdownMenuItem(value: s, child: Text(s.name)))
                            .toList(),
                        onChanged: (value) {
                          if (value != null) setState(() => scenario = value);
                        }),
                  ]))),
          Expanded(
              child: OdessyApp(
                  key: ValueKey(scenario), api: ScenarioApi(scenario))),
        ])),
      );
}
