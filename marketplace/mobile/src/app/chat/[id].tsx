import { Stack, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { FlatList, KeyboardAvoidingView, Platform, Text, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button, ErrorText, styles } from '../../components/ui';
import { api } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { formatDate, useT } from '../../lib/i18n';
import { colors, radius } from '../../lib/theme';
import type { Message } from '../../lib/types';

const POLL_MS = 4000;

export default function ChatScreen() {
  const { id, title } = useLocalSearchParams<{ id: string; title?: string }>();
  const { user } = useAuth();
  const { t, lang } = useT();
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const lastId = useRef(0);
  const list = useRef<FlatList<Message>>(null);

  const poll = useCallback(async () => {
    try {
      const fresh = await api<Message[]>(`/chats/${id}/messages?after_id=${lastId.current}`);
      if (fresh.length) {
        lastId.current = fresh[fresh.length - 1].id;
        setMessages((m) => [...m, ...fresh.filter((f) => !m.some((x) => x.id === f.id))]);
      }
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => clearInterval(timer);
  }, [poll]);

  async function send() {
    const body = text.trim();
    if (!body) return;
    setText('');
    try {
      await api<Message>(`/chats/${id}/messages`, { body: { text: body } });
      await poll();
    } catch (e) {
      setText(body);
      setError((e as Error).message);
    }
  }

  return (
    <SafeAreaView style={styles.screen} edges={['bottom']}>
      <Stack.Screen options={{ title: title || t('chat') }} />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined} keyboardVerticalOffset={90}>
        <FlatList
          ref={list}
          contentContainerStyle={styles.content}
          data={messages}
          keyExtractor={(m) => String(m.id)}
          onContentSizeChange={() => list.current?.scrollToEnd({ animated: false })}
          renderItem={({ item }) => {
            const mine = item.sender_id === user?.id;
            return (
              <View
                style={{
                  alignSelf: mine ? 'flex-end' : 'flex-start',
                  backgroundColor: mine ? colors.primary : colors.card,
                  borderRadius: radius,
                  padding: 10,
                  marginBottom: 8,
                  maxWidth: '80%',
                }}
              >
                <Text style={{ color: mine ? '#fff' : colors.text, fontSize: 15 }}>{item.text}</Text>
                <Text style={{ color: mine ? '#DCE6FF' : colors.muted, fontSize: 11, marginTop: 4 }}>
                  {formatDate(item.created_at, lang)}
                </Text>
              </View>
            );
          }}
        />
        <ErrorText text={error} />
        <View style={[styles.row, { padding: 8, gap: 8, backgroundColor: colors.card }]}>
          <TextInput
            style={[styles.input, { flex: 1 }]}
            value={text}
            onChangeText={setText}
            placeholder={t('message')}
            placeholderTextColor={colors.muted}
            multiline
          />
          <Button title="➤" onPress={send} style={{ paddingHorizontal: 18 }} disabled={!text.trim()} />
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
