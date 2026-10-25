// 核对优先：主信息是通知全文与事项；次信息是日期/材料；只展示必要状态。
// 主操作随状态从“提取信息”切换为“确认保存”；新建与配置为次操作。
// 灰度先行，使用 Qt 系统控件与中性色；间距 8/16/24，字号 12/14/18/24。
// 材料名称为主，完整限定说明为正文；修正和独立依据窗口分开，沿用Fusion表单与灰度。
import QtQuick
import QtQuick.Controls.Fusion
import QtQuick.Layouts

ApplicationWindow {
    id: window
    visible: true
    width: 1180
    height: 820
    minimumWidth: 960
    minimumHeight: 700
    title: "校园通知助手"
    color: "#f5f5f5"
    font.family: "Microsoft YaHei UI"
    font.pixelSize: 14
    property bool allowClose: false
    property string pendingAction: ""
    property string pendingTodo: ""
    property bool hasResult: Object.keys(backend.resultData).length > 0

    function proceed(action, todoId) {
        if (action === "close") {
            allowClose = true
            window.close()
        } else {
            backend.clearDraft()
            if (action === "open") backend.openTodo(todoId)
        }
    }
    function requestAction(action, todoId) {
        if (backend.unsaved) {
            pendingAction = action
            pendingTodo = todoId || ""
            discardDialog.open()
        } else proceed(action, todoId || "")
    }
    onClosing: function(close) {
        if (allowClose) return
        close.accepted = false
        if (backend.busy) backend.notifyBusyClose()
        else requestAction("close", "")
    }

    component CitedField: ColumnLayout {
        id: citedField
        property string fieldKey: ""
        property string statusLabel: ""
        property string heading: ""
        property string content: ""
        property string evidence: ""
        property string requirement: ""
        property string materialRelation: ""
        property string requirementName: ""
        property string valueName: ""
        property string evidenceName: ""
        property bool evidenceMatched: false
        property bool userSupplied: false
        spacing: 8
        Layout.fillWidth: true
        Label { visible: heading.length > 0; text: heading; color: "#525252"; font.pixelSize: 12 }
        Label {
            objectName: valueName
            text: content
            textFormat: Text.PlainText
            font.pixelSize: 18
            font.weight: Font.DemiBold
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: backend.locateEvidence(citedField.fieldKey) }
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        Label {
            objectName: requirementName
            visible: requirement.length > 0
            text: requirement
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        Label {
            objectName: evidenceName
            visible: statusLabel.length > 0
            text: statusLabel
            textFormat: Text.PlainText
            color: "#525252"
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
        RowLayout {
            spacing: 8
            Button {
                objectName: citedField.fieldKey === "action" ? "actionCorrection" : "correction_" + citedField.fieldKey
                text: "修正"
                visible: backend.unsaved && window.hasResult
                enabled: !backend.busy
                onClicked: {
                    correctionDialog.fieldKey = citedField.fieldKey
                    correctionValue.text = citedField.content
                    correctionRequirement.text = citedField.requirement
                    correctionRelation.currentIndex = Math.max(0, correctionRelation.indexOfValue(citedField.materialRelation || "uncertain"))
                    supplement.checked = citedField.userSupplied
                    correctionDialog.open()
                }
            }
            Button {
                objectName: "chooseEvidence_" + citedField.fieldKey
                text: backend.unsaved ? "选择依据" : "查看依据"
                enabled: !backend.busy
                onClicked: {
                    evidenceDialog.fieldKey = citedField.fieldKey
                    evidenceInput.deselect()
                    evidenceDialog.open()
                    Qt.callLater(function() {
                        if (citedField.evidenceMatched) backend.locateEvidence(citedField.fieldKey)
                    })
                }
            }
        }
    }

    Connections {
        target: backend
        function onEvidenceSelected(start, end) {
            if (evidenceDialog.opened) {
                evidenceInput.forceActiveFocus()
                evidenceInput.select(start, end)
            } else {
                sourceInput.forceActiveFocus()
                sourceInput.select(start, end)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 16
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                spacing: 8
                Label { text: "校园通知助手"; font.pixelSize: 24; font.bold: true }
                Label { text: "粘贴一条通知，核对原文，再保存为待办。"; color: "#525252" }
            }
            Item { Layout.fillWidth: true }
            Label { text: backend.keyConfigured ? "API Key 已配置" : "尚未配置 API Key"; color: "#525252" }
            Button { objectName: "keyButton"; text: "配置 API Key"; enabled: !backend.busy; onClicked: keyDialog.open() }
            Button {
                objectName: "newButton"
                text: "新建 / 清空"
                enabled: !backend.busy
                onClicked: window.requestAction("new", "")
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 16
            Frame {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                padding: 16
                background: Rectangle { color: "white"; radius: 8 }
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 16
                    RowLayout {
                        Label { text: "通知原文"; font.pixelSize: 18; font.bold: true }
                        Button { text: "修改原文"; enabled: !backend.busy && window.hasResult && backend.unsaved; onClicked: { sourceEdit.text = backend.sourceText; sourceEditDialog.open() } }
                    }
                    Label {
                        text: "保留完整内容，逐项对照右侧信息。"
                        color: "#525252"
                    }
                    ScrollView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        TextArea {
                            id: sourceInput
                            objectName: "sourceInput"
                            text: backend.sourceText
                            readOnly: backend.readOnly
                            selectByMouse: true
                            persistentSelection: true
                            textFormat: TextEdit.PlainText
                            wrapMode: TextEdit.Wrap
                            placeholderText: "在这里粘贴通知全文…\n\n支持一条办理事项；缺失信息与待确认时间可保存。"
                            onTextChanged: if (!readOnly) backend.updateSource(text)
                        }
                    }
                }
            }
            Frame {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 1
                padding: 16
                background: Rectangle { color: "white"; radius: 8 }
                ColumnLayout {
                    anchors.fill: parent
                    spacing: 16
                    Label { text: backend.resultHeading; font.pixelSize: 18; font.bold: true }
                    ScrollView {
                        objectName: "resultsScrollView"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        ColumnLayout {
                            width: parent.width
                            spacing: 24
                            Label {
                                visible: !window.hasResult
                                text: "提取后在这里核对事项、截止日期和材料。\n未经确认的信息不会生成待办。"
                                wrapMode: Text.Wrap
                                color: "#525252"
                                Layout.fillWidth: true
                            }
                            CitedField {
                                visible: window.hasResult
                                heading: "事项"
                                fieldKey: "action"
                                statusLabel: backend.resultData.action ? backend.resultData.action.label : ""
                                content: backend.resultData.action ? backend.resultData.action.value : ""
                                evidence: backend.resultData.action ? backend.resultData.action.evidence : ""
                                valueName: "actionValue"
                                evidenceMatched: !!backend.resultData.action && backend.resultData.action.review === "matched" && backend.resultData.action.origin !== "user"
                                userSupplied: !!backend.resultData.action && backend.resultData.action.origin === "user"
                            }
                            CitedField {
                                visible: window.hasResult
                                heading: "截止时间"
                                fieldKey: "deadline"
                                statusLabel: backend.resultData.deadline ? backend.resultData.deadline.label : ""
                                content: backend.resultData.deadline ? backend.resultData.deadline.value : ""
                                evidence: backend.resultData.deadline ? backend.resultData.deadline.evidence : ""
                                valueName: "deadlineValue"
                                evidenceName: "deadlineEvidence"
                                evidenceMatched: !!backend.resultData.deadline && backend.resultData.deadline.review === "matched" && backend.resultData.deadline.origin !== "user"
                                userSupplied: !!backend.resultData.deadline && backend.resultData.deadline.origin === "user"
                            }
                            ColumnLayout {
                                visible: window.hasResult
                                spacing: 16
                                Layout.fillWidth: true
                                Label { text: "所需材料"; font.pixelSize: 12; color: "#525252" }
                                Label {
                                    objectName: "materialsMissing"
                                    visible: (backend.resultData.materials || []).length === 0
                                    text: "原文未提及"
                                }
                                Repeater {
                                    model: backend.resultData.materials || []
                                    CitedField {
                                        required property var modelData
                                        required property int index
                                        fieldKey: "materials." + index
                                        statusLabel: modelData.label
                                        content: modelData.value
                                        requirement: modelData.requirement || ""
                                        materialRelation: modelData.relation || ""
                                        requirementName: "materialRequirement_" + index
                                        valueName: "materialValue_" + index
                                        evidenceName: "materialEvidence_" + index
                                        evidence: modelData.evidence
                                        evidenceMatched: modelData.review === "matched" && modelData.origin !== "user"
                                        userSupplied: modelData.origin === "user"
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            BusyIndicator {
                objectName: "loadingIndicator"
                running: backend.busy
                visible: running
                Layout.preferredWidth: 32
                Layout.preferredHeight: 32
            }
            Label {
                objectName: "statusMessage"
                text: backend.message
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                Layout.fillWidth: true
                Accessible.role: Accessible.StaticText
            }
            Button {
                objectName: "extractButton"
                text: backend.busy ? "处理中…" : "提取信息"
                enabled: !backend.busy && !backend.readOnly && sourceInput.text.trim().length > 0
                highlighted: !window.hasResult
                onClicked: backend.extract()
            }
            Button {
                objectName: "confirmButton"
                text: "已核对，确认保存"
                enabled: backend.canSave
                highlighted: window.hasResult
                onClicked: backend.confirm()
            }
        }
        Label { text: "待办清单 · " + backend.todos.length; font.pixelSize: 18; font.bold: true }
        ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: 104
            clip: true
            ListView {
                id: todoList
                model: backend.todos
                spacing: 8
                delegate: Button {
                    objectName: "todoButton"
                    required property var modelData
                    width: todoList.width
                    text: modelData.title + "    |    截止 " + modelData.date + "    |    查看原文与依据"
                    enabled: !backend.busy
                    onClicked: window.requestAction("open", modelData.id)
                }
                Label { visible: backend.todos.length === 0; text: "尚无待办，确认保存后会显示在这里。"; color: "#525252" }
            }
        }
        Label {
            text: "本地数据：" + backend.dataPath
            textFormat: Text.PlainText
            font.pixelSize: 12
            color: "#525252"
            wrapMode: Text.WrapAnywhere
            Layout.fillWidth: true
        }
    }

    Connections {
        target: backend
        function onChanged() {
            if (backend.duplicatePending && !duplicateListDialog.visible) duplicateDialog.open()
            else if (!backend.duplicatePending) duplicateDialog.close()
        }
    }
    Dialog {
        id: duplicateDialog
        objectName: "duplicateDialog"
        title: "这条通知已有待办"
        anchors.centerIn: parent
        modal: true
        width: 480
        closePolicy: Popup.NoAutoClose
        contentItem: ColumnLayout {
            spacing: 16
            Label { text: "找到 " + backend.duplicateTodos.length + " 条已有待办。可直接回查，也可重新提取并另行确认保存。"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            Button { objectName: "duplicateViewButton"; text: "查看已有待办"; onClicked: { duplicateDialog.close(); duplicateListDialog.open() } }
            Button { objectName: "duplicateContinueButton"; text: "继续重新提取"; onClicked: backend.reextractDuplicate() }
            Button { objectName: "duplicateCancelButton"; text: "取消本次操作"; onClicked: backend.cancelDuplicate() }
        }
    }
    Dialog {
        id: duplicateListDialog
        objectName: "duplicateListDialog"
        property int matchCount: backend.duplicateTodos.length
        title: "该通知已有待办 · " + matchCount
        anchors.centerIn: parent
        modal: true
        width: 560
        height: Math.min(480, 160 + matchCount * 56)
        standardButtons: Dialog.Close
        onRejected: backend.cancelDuplicate()
        contentItem: ScrollView {
            clip: true
            ListView {
                id: duplicateList
                model: backend.duplicateTodos
                spacing: 8
                delegate: Button {
                    objectName: "duplicateTodoButton"
                    required property var modelData
                    width: duplicateList.width
                    text: modelData.title + " · 查看原文与依据"
                    onClicked: { backend.openDuplicate(modelData.id); duplicateListDialog.close() }
                }
            }
        }
    }

    Dialog {
        id: evidenceDialog
        objectName: "evidenceDialog"
        property string fieldKey: ""
        title: backend.unsaved ? "选择依据" : "查看依据"
        anchors.centerIn: parent
        modal: true
        width: Math.min(760, window.width - 80)
        height: Math.min(600, window.height - 80)
        contentItem: ColumnLayout {
            spacing: 16
            Label {
                visible: backend.unsaved
                text: "选中支持该字段的完整原文片段。"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                TextArea {
                    id: evidenceInput
                    objectName: "evidenceInput"
                    text: backend.sourceText
                    readOnly: true
                    selectByMouse: true
                    persistentSelection: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    Accessible.name: "选择原文依据"
                }
            }
        }
        footer: DialogButtonBox {
            Button {
                objectName: "evidenceApply"
                text: "使用所选文字"
                visible: backend.unsaved
                enabled: !backend.busy && evidenceInput.selectionEnd > evidenceInput.selectionStart
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            Button {
                text: backend.unsaved ? "取消" : "关闭"
                DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            }
        }
        onAccepted: if (backend.unsaved) backend.selectEvidence(fieldKey, evidenceInput.selectionStart, evidenceInput.selectionEnd)
    }
    Dialog {
        id: correctionDialog
        objectName: "correctionDialog"
        property string fieldKey: ""
        readonly property bool material: fieldKey.startsWith("materials.")
        title: "修正字段"
        anchors.centerIn: parent
        modal: true
        width: 460
        standardButtons: Dialog.Save | Dialog.Cancel
        ColumnLayout {
            width: parent.width
            spacing: 8
            Label { text: correctionDialog.material ? "材料名称（原文用词）" : "内容" }
            TextField { id: correctionValue; objectName: "correctionValue"; Layout.fillWidth: true; Accessible.name: correctionDialog.material ? "材料名称" : "字段内容" }
            Label { visible: correctionDialog.material; text: "完整材料说明（条件、阶段、处理方式、或／且关系）"; wrapMode: Text.Wrap; Layout.fillWidth: true }
            ScrollView {
                visible: correctionDialog.material
                Layout.fillWidth: true
                Layout.preferredHeight: 120
                contentWidth: availableWidth
                TextArea {
                    id: correctionRequirement
                    objectName: "correctionRequirement"
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.Wrap
                    selectByMouse: true
                    Accessible.name: "完整材料说明"
                }
            }
            Label { visible: correctionDialog.material; text: "组内材料关系" }
            ComboBox {
                id: correctionRelation
                objectName: "correctionRelation"
                visible: correctionDialog.material
                Layout.fillWidth: true
                textRole: "text"
                valueRole: "value"
                Accessible.name: "组内材料关系"
                model: [
                    {text: "待核对", value: "uncertain"},
                    {text: "单份材料", value: "single"},
                    {text: "任选其一", value: "any"},
                    {text: "全部需要", value: "all"}
                ]
            }
            CheckBox { id: supplement; objectName: "supplement"; text: "这是用户补充内容" }
            Button {
                text: "移除此字段"
                enabled: correctionDialog.fieldKey !== "action"
                onClicked: { backend.correctField(correctionDialog.fieldKey, "", false, true, 0, 0); correctionDialog.close() }
            }
        }
        onAccepted: backend.correctField(fieldKey, correctionValue.text, supplement.checked, false, -1, -1, correctionRequirement.text, correctionRelation.currentValue)
    }
    Dialog {
        id: sourceEditDialog
        title: "修改原文将使旧提取结果失效"
        anchors.centerIn: parent
        width: 600
        modal: true
        standardButtons: Dialog.Save | Dialog.Cancel
        TextArea { id: sourceEdit; width: parent.width; wrapMode: TextEdit.Wrap; selectByMouse: true }
        onAccepted: backend.replaceSource(sourceEdit.text)
    }
    Dialog {
        id: keyDialog
        objectName: "keyDialog"
        title: "配置个人 DeepSeek API Key"
        anchors.centerIn: parent
        modal: true
        width: 480
        standardButtons: Dialog.Save | Dialog.Cancel
        ColumnLayout {
            width: parent.width
            spacing: 16
            Label {
                text: "密钥仅保存到 Windows 凭据存储。\n每次提取只向 DeepSeek 发送本次通知。"
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            TextField {
                id: secretInput
                objectName: "secretInput"
                Layout.fillWidth: true
                echoMode: TextInput.Password
                inputMethodHints: Qt.ImhHiddenText | Qt.ImhNoPredictiveText
                placeholderText: "输入新的 API Key"
                Accessible.name: "个人 DeepSeek API Key"
            }
        }
        onOpened: secretInput.forceActiveFocus()
        onAccepted: { backend.configureKey(secretInput.text); secretInput.clear() }
        onRejected: secretInput.clear()
    }
    Dialog {
        id: discardDialog
        objectName: "discardDialog"
        title: "丢弃未保存内容？"
        anchors.centerIn: parent
        modal: true
        width: 440
        Label { text: "当前输入与提取草稿尚未保存，丢弃后无法恢复。"; wrapMode: Text.Wrap; width: parent.width }
        footer: DialogButtonBox {
            Button { text: "返回核对"; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
            Button { text: "丢弃"; DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole }
        }
        onAccepted: window.proceed(window.pendingAction, window.pendingTodo)
    }
}
