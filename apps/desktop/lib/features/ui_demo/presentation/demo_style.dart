import 'package:flutter/material.dart';

enum DemoDestination {
  assistant,
  workspaces,
  devices,
  activityUsage,
  jobs,
  handoffs,
  installation,
  maintenance,
  pairing,
  settings
}

class DemoCard extends StatelessWidget {
  const DemoCard(
      {super.key,
      required this.title,
      required this.child,
      this.description,
      this.icon});
  final String title;
  final String? description;
  final IconData? icon;
  final Widget child;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(bottom: 18),
        padding: const EdgeInsets.all(22),
        decoration: BoxDecoration(
            color: DemoColors.surface,
            border: Border.all(color: DemoColors.line),
            borderRadius: BorderRadius.circular(12)),
        child: Material(
            type: MaterialType.transparency,
            child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(children: [
                    if (icon != null) ...[
                      Icon(icon, color: DemoColors.blue, size: 20),
                      const SizedBox(width: 10)
                    ],
                    Expanded(
                        child: Text(title,
                            style: const TextStyle(
                                fontSize: 16, fontWeight: FontWeight.w700)))
                  ]),
                  if (description != null)
                    Padding(
                        padding: const EdgeInsets.only(top: 8),
                        child: Text(description!,
                            style: const TextStyle(
                                color: DemoColors.muted,
                                fontSize: 12,
                                height: 1.7))),
                  const SizedBox(height: 18),
                  child,
                ])),
      );
}

class DemoValueRow extends StatelessWidget {
  const DemoValueRow(this.label, this.value,
      {super.key, this.color = DemoColors.ink});
  final String label, value;
  final Color color;
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: LayoutBuilder(
            builder: (context, size) => size.maxWidth < 420
                ? Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                        Text(label,
                            style: const TextStyle(
                                fontSize: 12, color: DemoColors.muted)),
                        const SizedBox(height: 5),
                        SelectableText(value,
                            style: TextStyle(fontSize: 13, color: color))
                      ])
                : Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Expanded(
                        child: Text(label,
                            style: const TextStyle(
                                fontSize: 12, color: DemoColors.muted))),
                    const SizedBox(width: 16),
                    Flexible(
                        child: SelectableText(value,
                            textAlign: TextAlign.end,
                            style: TextStyle(fontSize: 13, color: color)))
                  ])),
      );
}

abstract final class DemoColors {
  static const background = Color(0xff111d25);
  static const sidebar = Color(0xff15252e);
  static const surface = Color(0xff1c303a);
  static const raised = Color(0xff243e49);
  static const line = Color(0xff36515e);
  static const ink = Color(0xffedf5f4);
  static const muted = Color(0xffb0c5cc);
  static const mint = Color(0xff9ce4c3);
  static const blue = Color(0xff9bcff5);
  static const amber = Color(0xffffd18a);
  static const red = Color(0xffffb2b2);
}

class DemoTag extends StatelessWidget {
  const DemoTag(this.text,
      {super.key, this.color = DemoColors.muted, this.icon});
  final String text;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
        decoration: BoxDecoration(
          color: color.withValues(alpha: .08),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: color.withValues(alpha: .25)),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 6),
          ],
          Flexible(
              child: Text(text, style: TextStyle(fontSize: 11, color: color))),
        ]),
      );
}

class DemoSectionLabel extends StatelessWidget {
  const DemoSectionLabel(this.text, {super.key, this.trailing});
  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(12, 18, 6, 8),
        child: Row(children: [
          Expanded(
              child: Text(text,
                  style: const TextStyle(
                      color: DemoColors.muted,
                      fontSize: 12,
                      fontWeight: FontWeight.w700))),
          if (trailing != null) trailing!,
        ]),
      );
}

void demoNotice(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}
