// THE settings surface: a proper modal built from the library's
// building blocks — Dialog + vertical Tabs + ScrollArea — with one
// section per concern.

import { useQuery } from "@tanstack/react-query";
import { Clock, Info, MessageSquareCode } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "../../api";
import { keys } from "../../lib/keys";
import { DateTimePrefs } from "./DateTimePrefs";
import { PromptTuning } from "./PromptTuning";
import { SystemInfo } from "./SystemInfo";

const SECTIONS = [
  { value: "preferences", label: "Date & time", icon: Clock },
  { value: "prompts", label: "Prompts", icon: MessageSquareCode },
  { value: "system", label: "System", icon: Info },
] as const;

export type SettingsSection = (typeof SECTIONS)[number]["value"];

export function SettingsDialog({
  open,
  onOpenChange,
  section,
  onSectionChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  section: SettingsSection;
  onSectionChange: (s: SettingsSection) => void;
}) {
  const { data: overview } = useQuery({
    queryKey: keys.settings(),
    queryFn: api.getSettingsOverview,
    enabled: open,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[80vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="border-b px-6 py-4">
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Preferences are saved on the server — every browser shows the same.
            The system section is read-only.
          </DialogDescription>
        </DialogHeader>
        <Tabs
          value={section}
          onValueChange={(v) => onSectionChange(v as SettingsSection)}
          orientation="vertical"
          className="flex min-h-0 flex-1 flex-row gap-0"
        >
          <TabsList className="flex h-full w-48 shrink-0 flex-col items-stretch justify-start gap-1 rounded-none border-r bg-muted/30 p-3">
            {SECTIONS.map((s) => (
              <TabsTrigger
                key={s.value}
                value={s.value}
                className="w-full justify-start gap-2 px-3 py-2 data-[state=active]:bg-background"
              >
                <s.icon className="size-4" />
                {s.label}
              </TabsTrigger>
            ))}
          </TabsList>
          <ScrollArea className="min-h-0 flex-1">
            <div className="p-6">
              <TabsContent value="preferences" className="mt-0">
                <DateTimePrefs />
              </TabsContent>
              <TabsContent value="prompts" className="mt-0">
                {overview && <PromptTuning defaults={overview.prompt_defaults} />}
              </TabsContent>
              <TabsContent value="system" className="mt-0">
                <SystemInfo />
              </TabsContent>
            </div>
          </ScrollArea>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
