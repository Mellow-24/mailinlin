'use client';

import { Sparkles } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost } from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';

const YISHUI_USE_CASE = '香港粤语玄学知识咨询';
const YISHUI_ACTIVITY_DESCRIPTION = `创建一个名为“易水 AI 顾问”的虚构语音角色。角色必须在开场明确说明自己是 AI 合成演示，并非任何真人本人。支持粤语和普通话，默认使用繁体中文与简短口语回答。

先询问用户想聊家居风水、流年生肖、事业选择或感情家庭中的哪一类，再根据知识库回答。回答应温和、克制，不使用确定性预测，不承诺结果；遇到医疗、法律、投资等高风险问题时说明能力边界。每次回答控制在两到三句，允许用户随时打断，并记住当前会话已经提供的称呼、主题和必要背景。`;

export default function CreateWorkflowPage() {
    const router = useRouter();
    const { user, getAccessToken } = useAuth();
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showSuccessModal, setShowSuccessModal] = useState(false);
    const [workflowId, setWorkflowId] = useState<string | null>(null);

    const [callType, setCallType] = useState<'inbound' | 'outbound'>('inbound');
    const [useCase, setUseCase] = useState('');
    const [activityDescription, setActivityDescription] = useState('');

    const handleCreateWorkflow = async () => {
        if (!useCase || !activityDescription) {
            setError('请填写完整信息');
            return;
        }

        if (!user) {
            setError('请先登录后再创建智能体');
            return;
        }

        setIsLoading(true);
        setError(null);

        try {
            const accessToken = await getAccessToken();

            // Call the API to create workflow from template
            const response = await createWorkflowFromTemplateApiV1WorkflowCreateTemplatePost({
                body: {
                    call_type: callType,
                    use_case: useCase,
                    activity_description: activityDescription,
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data?.id) {
                setWorkflowId(String(response.data.id));
                setShowSuccessModal(true);
            }
        } catch (err) {
            setError('创建失败，请稍后再试。');
            logger.error(`Error creating workflow: ${err}`);
        } finally {
            setIsLoading(false);
        }
    };

    const handleModalContinue = () => {
        if (!workflowId) return;
        router.push(`/workflow/${workflowId}?onboarding=web_call`);
    };

    return (
        <div className="min-h-screen">
            <div className="container mx-auto px-4 py-8 max-w-2xl">
                <div className="mb-6">
                    <h1 className="text-3xl font-bold mb-2">创建语音智能体</h1>
                    <p className="text-muted-foreground">
                        描述咨询场景，系统会生成一套可继续编辑的语音流程
                    </p>
                </div>

                <Card>
                    <CardHeader>
                        <CardTitle>智能体信息</CardTitle>
                        <CardDescription>
                            可先套用易水 AI 演示预设，再按实际业务修改
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-6">
                        <Button
                            type="button"
                            variant="outline"
                            className="w-full justify-start border-amber-500/30 bg-amber-500/[0.06]"
                            onClick={() => {
                                setCallType('inbound');
                                setUseCase(YISHUI_USE_CASE);
                                setActivityDescription(YISHUI_ACTIVITY_DESCRIPTION);
                                setError(null);
                            }}
                        >
                            <Sparkles className="mr-2 h-4 w-4 text-amber-500" />
                            套用“易水 AI 粤语顾问”演示预设
                        </Button>

                        <div className="space-y-2">
                            <Label htmlFor="call-type">通话类型</Label>
                            <Select value={callType} onValueChange={(value) => setCallType(value as 'inbound' | 'outbound')}>
                                <SelectTrigger id="call-type">
                                    <SelectValue placeholder="Select type" />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="inbound">
                                        用户发起咨询
                                    </SelectItem>
                                    <SelectItem value="outbound">
                                        AI 主动外呼
                                    </SelectItem>
                                </SelectContent>
                            </Select>
                            <p className="text-sm text-muted-foreground">
                                网页演示建议选择“用户发起咨询”
                            </p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="use-case">使用场景</Label>
                            <Input
                                id="use-case"
                                placeholder="例如：香港粤语玄学知识咨询"
                                value={useCase}
                                onChange={(e) => setUseCase(e.target.value)}
                            />
                            <p className="text-sm text-muted-foreground">
                                用一句话描述智能体的主要用途
                            </p>
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="activity-description">角色与任务说明</Label>
                            <Textarea
                                id="activity-description"
                                placeholder="说明角色语气、开场、咨询流程、知识边界和结束方式。"
                                value={activityDescription}
                                onChange={(e) => setActivityDescription(e.target.value)}
                                className="min-h-[100px]"
                            />
                            <p className="text-sm text-muted-foreground">
                                这些内容会用于生成语音智能体的系统提示词
                            </p>
                        </div>

                        {error && (
                            <p className="text-sm text-red-500">{error}</p>
                        )}

                        <div className="pt-4">
                            <Button
                                onClick={handleCreateWorkflow}
                                disabled={isLoading || !useCase || !activityDescription}
                                className="w-full"
                            >
                                {isLoading ? '正在创建...' : '创建智能体'}
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Loading Overlay */}
            {isLoading && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
                    <Card className="w-full max-w-md p-8">
                        <div className="flex flex-col items-center space-y-6">
                            {/* Animated spinner */}
                            <div className="relative">
                                <div className="w-16 h-16 border-4 border-muted rounded-full"></div>
                                <div className="absolute top-0 left-0 w-16 h-16 border-4 border-transparent border-t-primary rounded-full animate-spin"></div>
                            </div>

                            <div className="text-center space-y-2">
                                <h3 className="text-lg font-semibold">
                                    正在生成咨询流程
                                </h3>
                                <p className="text-sm text-muted-foreground max-w-xs">
                                    正在配置角色、开场和对话节点，请稍候片刻……
                                </p>
                            </div>
                        </div>
                    </Card>
                </div>
            )}

            {/* Success Modal */}
            <Dialog open={showSuccessModal} onOpenChange={setShowSuccessModal}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <svg className="w-5 h-5 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            智能体创建成功
                        </DialogTitle>
                        <DialogDescription asChild>
                            <div className="mt-4 space-y-3">
                                <p>
                                    已根据你的说明生成语音流程，可继续调整每个节点的提示词和转场条件。
                                </p>
                                <p>
                                    下一步请在“模型与语音”中应用香港粤语预设，并填写自己的模型服务密钥。
                                </p>
                                <p>
                                    角色必须保留 AI 身份披露；未经授权不要使用真人姓名、肖像或克隆声音。
                                </p>
                            </div>
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="mt-6">
                        <Button
                            onClick={handleModalContinue}
                            className="w-full"
                        >
                            打开并测试智能体
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
